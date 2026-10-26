import asyncio
import logging
import time
from typing import AsyncGenerator, Dict, Any, List, Optional
from app.config import settings
from app.auth import AuthenticatedUser
from app.agent.prompts import build_system_prompt
from app.tools.factory_data import FactoryDataTool
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ModelExecutionResult:
    def __init__(self):
        self.text: str = ""
        self.input_tokens: Optional[int] = None
        self.output_tokens: Optional[int] = None
        self.total_tokens: Optional[int] = None
        self.latency_ms: float = 0.0
        self.retry_count: int = 0
        self.status: str = "success"
        self.error_category: Optional[str] = None


class ModelService:
    def __init__(self):
        self.api_key = settings.effective_api_key
        self.model_name = settings.MODEL_NAME
        self.timeout = settings.MODEL_TIMEOUT_SECONDS
        self.max_retries = settings.MODEL_MAX_RETRIES
        self.last_retry_count = 0

    def _determine_factory_query(self, user_message: str) -> Optional[Dict[str, Any]]:
        """
        Determines if and which factory data operation should be invoked.
        Provides robust routing for operational questions.
        """
        msg = user_message.lower().strip()

        # Count operations
        if any(phrase in msg for phrase in ["how many", "count", "number of scans", "scans recorded"]):
            scan_type = None
            if "qc" in msg:
                scan_type = "qc_check"
            elif "assembly" in msg:
                scan_type = "assembly"
            elif "shipping" in msg:
                scan_type = "shipping"
            elif "sorting" in msg:
                scan_type = "sorting"
            return {"operation": "get_scan_count", "scan_type": scan_type}

        # Piece operations
        if any(phrase in msg for phrase in ["piece", "pieces", "associated", "equipment", "parts", "part"]):
            return {"operation": "get_associated_pieces", "limit": 20}

        # Summary operations
        if any(phrase in msg for phrase in ["summarise", "summarize", "summary", "breakdown", "overview", "report"]):
            return {"operation": "get_scans_summary"}

        # Recent scans list operations
        if any(phrase in msg for phrase in ["scan", "scans", "recent", "show", "list", "records", "view", "inspections"]):
            scan_type = None
            if "qc" in msg:
                scan_type = "qc_check"
            elif "assembly" in msg:
                scan_type = "assembly"
            elif "shipping" in msg:
                scan_type = "shipping"
            elif "sorting" in msg:
                scan_type = "sorting"
            return {"operation": "get_recent_scans", "limit": 20, "scan_type": scan_type}

        return None

    def _is_transient_error(self, exc: Exception) -> bool:
        """Determines if an error is eligible for retry."""
        if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
            return True
        err_msg = str(exc).lower()
        transient_indicators = [
            "timeout", "timed out", "503", "500", "overloaded",
            "temporarily unavailable", "connection reset", "connection refused"
        ]
        return any(ind in err_msg for ind in transient_indicators)

    def _is_fallback_error(self, exc: Exception) -> bool:
        """Checks for permanent errors or quota exhaustion where local synthesis should take over."""
        err_msg = str(exc).lower()
        fallback_indicators = [
            "api_key_invalid", "api key not valid", "unauthorized", "401",
            "resource_exhausted", "quota exceeded", "429", "not found",
            "not_found", "404", "no longer available"
        ]
        return any(ind in err_msg for ind in fallback_indicators)

    async def _call_provider_api(self, prompt: str, system_instruction: str) -> Any:
        """
        Invokes upstream LLM API. If credentials fail or quota is exhausted,
        falls back gracefully to deterministic factory synthesis.
        """
        if not self.api_key:
            return "NO_API_KEY"

        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_instruction
            )
            return await asyncio.wait_for(
                model.generate_content_async(prompt, stream=True),
                timeout=self.timeout
            )
        except Exception as e:
            if self._is_fallback_error(e):
                logger.warning(f"Upstream LLM provider returned: {e}. Falling back to deterministic factory data response.")
                return "NO_API_KEY"
            raise e

    async def execute_model_call(self, prompt: str, system_instruction: str) -> Any:
        """
        Executes model call with automatic bounded single retry for transient failures.
        Returns the raw response stream or fallback token.
        """
        self.last_retry_count = 0
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                return await self._call_provider_api(prompt, system_instruction)
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries and self._is_transient_error(e):
                    self.last_retry_count += 1
                    logger.warning(f"Transient error on model call attempt {attempt + 1}: {e}. Retrying once...")
                    await asyncio.sleep(0.1)
                    continue
                break

        raise last_exception

    async def stream_chat(
        self,
        user_message: str,
        history: List[Dict[str, str]],
        user: AuthenticatedUser,
        db: AsyncSession
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Orchestrates tool retrieval, prompt construction, model streaming, and metrics tracking.
        """
        start_time = time.perf_counter()
        result = ModelExecutionResult()

        # Step 1: Check and execute the safe, read-only factory data tool
        tool_query = self._determine_factory_query(user_message)
        tool_data = None
        if tool_query:
            tool_data = await FactoryDataTool.execute(
                user=user,
                db=db,
                operation=tool_query["operation"],
                limit=tool_query.get("limit", 10),
                scan_type=tool_query.get("scan_type"),
                target_employee_id=tool_query.get("target_employee_id")
            )

        # Step 2: Build prompts with untrusted data isolation
        system_prompt = build_system_prompt(user.user_id, user.name, user.role)

        context_parts = []
        if history:
            context_parts.append("Previous Conversation History:")
            for msg in history:
                role_label = "User" if msg["role"] == "user" else "Assistant"
                context_parts.append(f"{role_label}: {msg['content']}")

        if tool_data is not None:
            context_parts.append("\n[RETRIEVED FACTORY DATA - UNTRUSTED CONTENT START]")
            context_parts.append(str(tool_data))
            context_parts.append("[RETRIEVED FACTORY DATA - UNTRUSTED CONTENT END]\n")

        context_parts.append(f"Current User Query: {user_message}")
        full_prompt = "\n".join(context_parts)

        # Step 3: Call Model Provider with Timeout & Retry
        accumulated_chunks = []
        try:
            raw_response = await self.execute_model_call(full_prompt, system_prompt)
            result.retry_count = self.last_retry_count

            if raw_response == "NO_API_KEY":
                fallback_text = self._synthesize_fallback(user, tool_data, user_message)
                for word in fallback_text.split(" "):
                    chunk = word + " "
                    accumulated_chunks.append(chunk)
                    yield {"type": "chunk", "content": chunk}
                    await asyncio.sleep(0.01)

                result.text = "".join(accumulated_chunks).strip()
                result.status = "success"
                result.input_tokens = len(full_prompt.split())
                result.output_tokens = len(result.text.split())
                result.total_tokens = result.input_tokens + result.output_tokens
            else:
                async for chunk in raw_response:
                    chunk_text = ""
                    try:
                        chunk_text = getattr(chunk, "text", "") or ""
                    except Exception:
                        if hasattr(chunk, "candidates") and chunk.candidates:
                            for c in chunk.candidates:
                                if hasattr(c, "content") and hasattr(c.content, "parts"):
                                    for p in c.content.parts:
                                        if hasattr(p, "text") and p.text:
                                            chunk_text += p.text
                    if chunk_text:
                        accumulated_chunks.append(chunk_text)
                        yield {"type": "chunk", "content": chunk_text}

                result.text = "".join(accumulated_chunks).strip()
                result.status = "success"
                if hasattr(raw_response, "usage_metadata") and raw_response.usage_metadata:
                    result.input_tokens = getattr(raw_response.usage_metadata, "prompt_token_count", None)
                    result.output_tokens = getattr(raw_response.usage_metadata, "candidates_token_count", None)
                    result.total_tokens = getattr(raw_response.usage_metadata, "total_token_count", None)
                else:
                    result.input_tokens = len(full_prompt.split())
                    result.output_tokens = len(result.text.split())
                    result.total_tokens = result.input_tokens + result.output_tokens

        except asyncio.TimeoutError:
            result.status = "failed"
            result.error_category = "TimeoutError"
            result.retry_count = 1
            logger.error("LLM model call timed out after retry.")
            yield {
                "type": "error",
                "error": "The AI service timed out while processing your request. Please try again.",
                "result": result
            }
            return
        except Exception as e:
            result.status = "failed"
            result.error_category = type(e).__name__
            result.retry_count = 1 if self._is_transient_error(e) else 0
            logger.error(f"LLM model call failed: {e}", exc_info=True)
            yield {
                "type": "error",
                "error": "The AI service is temporarily unavailable. Please try again later.",
                "result": result
            }
            return
        finally:
            result.latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

        yield {"type": "final", "result": result}

    def _synthesize_fallback(
        self,
        user: AuthenticatedUser,
        tool_data: Optional[Dict[str, Any]],
        user_message: str
    ) -> str:
        """
        Synthesizes factory response deterministically from verified tool data.
        Guarantees prompt injection strings in notes are rendered harmlessly as plain data.
        """
        if tool_data is None:
            return "I am your factory Ops Assistant. I can help answer questions about your scans, equipment pieces, and operational counts."

        if "total_scans" in tool_data:
            if user.role == "manager":
                return f"There are {tool_data['total_scans']} scans recorded in the factory system ({tool_data['scope']})."
            return f"You have recorded {tool_data['total_scans']} scans in the factory system."

        if "pieces" in tool_data:
            pieces = tool_data["pieces"]
            if not pieces:
                return "No associated equipment pieces were found for your scans."
            items = ", ".join([f"{p['piece_code']} ({p['piece_type']})" for p in pieces])
            return f"The equipment pieces associated with your scans are: {items}."

        if "breakdown" in tool_data:
            breakdown = tool_data["breakdown"]
            if not breakdown:
                return "No scan summary records found."
            items = [f"{b['scan_type']} ({b['status']}): {b['count']}" for b in breakdown]
            return f"Factory scans summary: {', '.join(items)}."

        if "scans" in tool_data:
            scans = tool_data["scans"]
            if not scans:
                return "No recent scans found for your authorized account."
            scan_lines = []
            for s in scans:
                note_text = s['note'] if s['note'] else "None"
                scan_lines.append(
                    f"Scan {s['scan_id']}: Piece {s['piece_code']} ({s['piece_type']}), "
                    f"Type: {s['scan_type']}, Status: {s['status']}, Note: \"{note_text}\""
                )
            return f"Here are the recent scans ({tool_data['scope']}):\n" + "\n".join(scan_lines)

        return "Retrieved factory data successfully."
