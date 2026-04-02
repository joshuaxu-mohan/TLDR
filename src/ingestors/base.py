from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class IngestResult:
    source_name: str
    source_type: str
    title: str
    content: str
    url: str
    published_at: datetime
    success: bool
    error_message: Optional[str] = field(default=None)
