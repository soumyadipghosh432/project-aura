import time
import secrets
import contextvars
from contextlib import contextmanager
from app.database import SessionLocal, TelemetryLog

trace_id_var = contextvars.ContextVar("trace_id", default=None)
span_id_var = contextvars.ContextVar("span_id", default=None)

class SpanContext:
    def __init__(self):
        self.token_count = 0
        self.message = ""

def start_trace():
    """Generates and sets a new trace ID if not already present."""
    if not trace_id_var.get():
        trace_id_var.set(secrets.token_hex(16)) # 32 character hex string
    return trace_id_var.get()

def clear_trace():
    """Clears the trace ID."""
    trace_id_var.set(None)

@contextmanager
def telemetry_span(event_name: str):
    """Context manager to measure execution time, capture exceptions, 
    and log synchronously to the telemetry database table.
    """
    start_time = time.perf_counter()
    
    current_trace = trace_id_var.get()
    if not current_trace:
        current_trace = secrets.token_hex(16)
        trace_id_var.set(current_trace)
        
    span_id = secrets.token_hex(8) # 16 character hex string
    token_token = span_id_var.set(span_id)
    
    ctx = SpanContext()
    exception_type = None
    status = "SUCCESS"
    
    try:
        yield ctx
    except Exception as e:
        status = "ERROR"
        exception_type = type(e).__name__
        ctx.message = str(e)
        raise e
    finally:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        db = SessionLocal()
        try:
            log = TelemetryLog(
                trace_id=current_trace,
                span_id=span_id,
                event_name=event_name,
                duration_ms=duration_ms,
                token_count=ctx.token_count,
                status=status,
                exception_type=exception_type,
                message=ctx.message or f"Completed {event_name}."
            )
            db.add(log)
            db.commit()
        except Exception as db_err:
            print(f"Failed to write telemetry: {db_err}")
        finally:
            db.close()
            span_id_var.reset(token_token)
