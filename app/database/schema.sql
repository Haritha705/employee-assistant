-- Ops Assistant Factory Schema (PostgreSQL Compatible)

CREATE TABLE IF NOT EXISTS employees (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('employee', 'manager')),
    department VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pieces (
    id VARCHAR(50) PRIMARY KEY,
    piece_code VARCHAR(50) NOT NULL,
    piece_type VARCHAR(50) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scans (
    id VARCHAR(50) PRIMARY KEY,
    piece_id VARCHAR(50) NOT NULL REFERENCES pieces(id) ON DELETE CASCADE,
    employee_id VARCHAR(50) NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    scan_type VARCHAR(50) NOT NULL,
    scanned_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    location VARCHAR(100) NOT NULL,
    note TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'completed'
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id SERIAL PRIMARY KEY,
    thread_id VARCHAR(100) NOT NULL,
    user_id VARCHAR(50) NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conv_thread_user ON conversation_history(thread_id, user_id);

CREATE TABLE IF NOT EXISTS request_metrics (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(100) NOT NULL,
    thread_id VARCHAR(100),
    user_id VARCHAR(50) NOT NULL,
    model_name VARCHAR(100),
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms DOUBLE PRECISION NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('success', 'failed')),
    retry_count INTEGER DEFAULT 0,
    error_category VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_metrics_created_at ON request_metrics(created_at);
CREATE INDEX IF NOT EXISTS idx_metrics_user_id ON request_metrics(user_id);
