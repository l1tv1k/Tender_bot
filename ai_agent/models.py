from pydantic import BaseModel, Field
from typing import Optional, List

class LaborCosts(BaseModel):
    shifts_hours: Optional[str] = Field(description="Количество часов/смен")
    guard_posts: Optional[int] = Field(description="Количество постов охраны")
    employees_per_shift: Optional[int] = Field(description="Количество сотрудников по сменам")
    schedule: Optional[str] = Field(description="График работы")

class Costs(BaseModel):
    hourly_rate: Optional[float] = Field(description="Стоимость часа")
    total_nmck: Optional[float] = Field(description="Общая НМЦК")
    articles_breakdown: Optional[str] = Field(description="Расшифровка по статьям")

class ProtectedObject(BaseModel):
    description: Optional[str] = Field(description="Что охраняется")
    equipment: Optional[List[str]] = Field(description="Оборудование (СКУД, Видео и т.д.)")

class Requirements(BaseModel):
    licenses: Optional[List[str]] = Field(description="Необходимые лицензии")
    personnel: Optional[str] = Field(description="Требования к персоналу")
    experience: Optional[str] = Field(description="Требования к опыту")

class Financials(BaseModel):
    application_security: Optional[str] = Field(description="Обеспечение заявки")
    contract_security: Optional[str] = Field(description="Обеспечение контракта")
    advance_payment: Optional[str] = Field(description="Аванс")
    penalties: Optional[str] = Field(description="Штрафы")

class Deadlines(BaseModel):
    execution_period: Optional[str] = Field(description="Сроки исполнения")
    app_deadline: Optional[str] = Field(description="Срок подачи заявок")

class TenderAnalysisResult(BaseModel):
    labor_costs: LaborCosts
    costs: Costs
    protected_object: ProtectedObject
    requirements: Requirements
    financials: Financials
    deadlines: Deadlines
    risks: Optional[List[str]] = Field(description="Выявленные риски")
    summary: str = Field(description="Краткая сводка (3-5 предложений)")