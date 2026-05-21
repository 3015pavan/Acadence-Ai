from typing import List, Optional

from pydantic import BaseModel, Field


class ResultItem(BaseModel):
    subject: str
    grade: str
    gp: Optional[float] = None

    class Config:
        from_attributes = True


class StudentItem(BaseModel):
    usn: str
    name: str
    sgpa: float
    pass_fail: str
    results: List[ResultItem] = Field(default_factory=list)

    class Config:
        from_attributes = True


class UploadResponse(BaseModel):
    total_students: int
    failed_count: int
    processed_file_url: str
    students: List[StudentItem]


class SummaryResponse(BaseModel):
    topper: Optional[StudentItem] = None
    average_sgpa: float
    total_students: int
    failed_count: int


class StudentTableResponse(BaseModel):
    students: List[StudentItem]


class DatasetDeleteResponse(BaseModel):
    dataset_id: int
    dataset_name: str
    source: str
    deleted_semesters: int
    deleted_results: int
    deleted_students: int
    files_removed: List[str] = Field(default_factory=list)
    remaining_students: int = 0


class ChatMessage(BaseModel):
    role: str
    content: str
    student_usns: List[str] = Field(default_factory=list)


class QueryRequest(BaseModel):
    query: str
    history: List[ChatMessage] = Field(default_factory=list)
    file_ids: List[int] = Field(default_factory=list)
    merge: Optional[str] = Field(default="union")


class QueryResponse(BaseModel):
    intent: Optional[str] = None
    answer: str
    students: List[StudentItem] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    suggestions: List[str] = Field(default_factory=list)
