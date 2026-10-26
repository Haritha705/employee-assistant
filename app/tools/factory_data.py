import logging
from typing import Dict, Any, List, Optional, Literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.auth import AuthenticatedUser

logger = logging.getLogger(__name__)

# Single approved operation allow-list
ApprovedOperation = Literal[
    "get_recent_scans",
    "get_scan_count",
    "get_associated_pieces",
    "get_scans_summary"
]

MAX_ROW_LIMIT = 50


class FactoryDataTool:
    """
    The single approved, read-only factory data access tool.
    Enforces role-based isolation, parameterized execution, and bounds checking.
    The model NEVER writes SQL. All queries are predefined and parameterized.
    """

    @staticmethod
    def get_tool_declaration() -> Dict[str, Any]:
        """Provides tool declaration/schema for LLM function calling."""
        return {
            "name": "query_factory_data",
            "description": (
                "Approved tool to query factory operations data (scans, pieces, summary metrics). "
                "The application strictly controls access based on the authenticated employee/manager role."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "get_recent_scans",
                            "get_scan_count",
                            "get_associated_pieces",
                            "get_scans_summary"
                        ],
                        "description": "The specific read operation to perform."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows to return (default 10, max 50)."
                    },
                    "scan_type": {
                        "type": "string",
                        "description": "Filter by scan type (e.g., 'assembly', 'qc_check', 'shipping', 'sorting')."
                    },
                    "target_employee_id": {
                        "type": "string",
                        "description": "Filter by specific employee ID (Manager role only. Ignored for standard employees)."
                    }
                },
                "required": ["operation"]
            }
        }

    @classmethod
    async def execute(
        cls,
        user: AuthenticatedUser,
        db: AsyncSession,
        operation: str,
        limit: int = 10,
        scan_type: Optional[str] = None,
        target_employee_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes an approved read-only factory data query.
        Guarantees that standard employees can ONLY access their own records.
        """
        # Enforce bounds
        safe_limit = max(1, min(int(limit or 10), MAX_ROW_LIMIT))
        
        # Enforce role-based employee scoping server-side
        if user.role != "manager":
            # For employee: strictly enforce their own user_id, ignoring any target_employee_id
            effective_employee_id = user.user_id
            is_manager_scope = False
        else:
            # For manager: can query specific employee or factory-wide
            effective_employee_id = target_employee_id if target_employee_id else None
            is_manager_scope = True

        try:
            if operation == "get_recent_scans":
                return await cls._get_recent_scans(db, effective_employee_id, is_manager_scope, scan_type, safe_limit)
            elif operation == "get_scan_count":
                return await cls._get_scan_count(db, effective_employee_id, is_manager_scope, scan_type)
            elif operation == "get_associated_pieces":
                return await cls._get_associated_pieces(db, effective_employee_id, is_manager_scope, safe_limit)
            elif operation == "get_scans_summary":
                return await cls._get_scans_summary(db, effective_employee_id, is_manager_scope)
            else:
                return {
                    "error": f"Invalid operation '{operation}'. Approved operations: {ApprovedOperation.__args__}"
                }
        except Exception as e:
            logger.error(f"Error executing factory query: {e}", exc_info=True)
            # Never expose internal database errors or SQL details
            return {"error": "A database access error occurred while processing the request."}

    @classmethod
    async def _get_recent_scans(
        cls,
        db: AsyncSession,
        employee_id: Optional[str],
        is_manager: bool,
        scan_type: Optional[str],
        limit: int
    ) -> Dict[str, Any]:
        query = """
            SELECT s.id, s.piece_id, p.piece_code, p.piece_type, s.employee_id, e.name as employee_name,
                   s.scan_type, s.scanned_at, s.location, s.note, s.status
            FROM scans s
            JOIN pieces p ON s.piece_id = p.id
            JOIN employees e ON s.employee_id = e.id
            WHERE 1=1
        """
        params: Dict[str, Any] = {"limit": limit}

        if employee_id:
            query += " AND s.employee_id = :emp_id"
            params["emp_id"] = employee_id
        elif not is_manager:
            # Fail-safe: employee must always filter by their id
            return {"scans": [], "count": 0}

        if scan_type:
            query += " AND s.scan_type = :scan_type"
            params["scan_type"] = scan_type

        query += " ORDER BY s.scanned_at DESC LIMIT :limit"

        stmt = text(query)
        result = await db.execute(stmt, params)
        rows = result.fetchall()

        records = [
            {
                "scan_id": r.id,
                "piece_id": r.piece_id,
                "piece_code": r.piece_code,
                "piece_type": r.piece_type,
                "employee_id": r.employee_id,
                "employee_name": r.employee_name,
                "scan_type": r.scan_type,
                "scanned_at": str(r.scanned_at),
                "location": r.location,
                "note": r.note,
                "status": r.status
            }
            for r in rows
        ]
        return {
            "scope": f"Filtered to employee {employee_id}" if employee_id else "Factory-wide (Manager scope)",
            "count": len(records),
            "scans": records
        }

    @classmethod
    async def _get_scan_count(
        cls,
        db: AsyncSession,
        employee_id: Optional[str],
        is_manager: bool,
        scan_type: Optional[str]
    ) -> Dict[str, Any]:
        query = "SELECT COUNT(*) FROM scans s WHERE 1=1"
        params: Dict[str, Any] = {}

        if employee_id:
            query += " AND s.employee_id = :emp_id"
            params["emp_id"] = employee_id
        elif not is_manager:
            return {"total_scans": 0}

        if scan_type:
            query += " AND s.scan_type = :scan_type"
            params["scan_type"] = scan_type

        stmt = text(query)
        result = await db.execute(stmt, params)
        count = result.scalar() or 0

        return {
            "scope": f"Employee {employee_id}" if employee_id else "Factory-wide",
            "total_scans": count,
            "scan_type_filter": scan_type or "all"
        }

    @classmethod
    async def _get_associated_pieces(
        cls,
        db: AsyncSession,
        employee_id: Optional[str],
        is_manager: bool,
        limit: int
    ) -> Dict[str, Any]:
        query = """
            SELECT DISTINCT p.id, p.piece_code, p.piece_type, p.description, p.status
            FROM pieces p
            JOIN scans s ON p.id = s.piece_id
            WHERE 1=1
        """
        params: Dict[str, Any] = {"limit": limit}

        if employee_id:
            query += " AND s.employee_id = :emp_id"
            params["emp_id"] = employee_id
        elif not is_manager:
            return {"pieces": [], "count": 0}

        query += " LIMIT :limit"

        stmt = text(query)
        result = await db.execute(stmt, params)
        rows = result.fetchall()

        pieces = [
            {
                "piece_id": r.id,
                "piece_code": r.piece_code,
                "piece_type": r.piece_type,
                "description": r.description,
                "status": r.status
            }
            for r in rows
        ]
        return {
            "scope": f"Pieces scanned by employee {employee_id}" if employee_id else "All scanned pieces",
            "count": len(pieces),
            "pieces": pieces
        }

    @classmethod
    async def _get_scans_summary(
        cls,
        db: AsyncSession,
        employee_id: Optional[str],
        is_manager: bool
    ) -> Dict[str, Any]:
        query = """
            SELECT s.scan_type, s.status, COUNT(*) as scan_count
            FROM scans s
            WHERE 1=1
        """
        params: Dict[str, Any] = {}

        if employee_id:
            query += " AND s.employee_id = :emp_id"
            params["emp_id"] = employee_id
        elif not is_manager:
            return {"breakdown": []}

        query += " GROUP BY s.scan_type, s.status ORDER BY s.scan_type"

        stmt = text(query)
        result = await db.execute(stmt, params)
        rows = result.fetchall()

        breakdown = [
            {"scan_type": r.scan_type, "status": r.status, "count": r.scan_count}
            for r in rows
        ]
        return {
            "scope": f"Summary for employee {employee_id}" if employee_id else "Factory-wide summary",
            "breakdown": breakdown
        }
