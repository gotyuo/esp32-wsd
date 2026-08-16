"""Pydantic 数据模型（API 输入/输出）。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ThresholdsIn(BaseModel):
    device_id: str = Field("*", description="设备编号，'*' 表示全局默认")
    temp_min: float = 5.0
    temp_max: float = 40.0
    hum_min: float = 20.0
    hum_max: float = 90.0
    pres_min: float = 950.0
    pres_max: float = 1050.0
    report_interval: int = Field(10, ge=3, le=3600)
    alarm_enabled: bool = True
    alarm_sound: bool = True


class IngestIn(BaseModel):
    """HTTP 直传（备用通道，测试或 MQTT 不可用时使用）。"""
    device_id: str
    temp: Optional[float] = None
    hum: Optional[float] = None
    pres: Optional[float] = None
    rssi: Optional[int] = None


class ThresholdsOut(BaseModel):
    device_id: str
    temp_min: float
    temp_max: float
    hum_min: float
    hum_max: float
    pres_min: float
    pres_max: float
    report_interval: int
    alarm_enabled: bool
    alarm_sound: bool
    updated_at: Optional[str] = None


# ================================================================ 认证
class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenOut(BaseModel):
    token: str
    user: dict


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field("", max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field("viewer", pattern=r"^(admin|viewer)$")


class PasswordChangeIn(BaseModel):
    old_password: str = Field(..., max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class SoundPrefIn(BaseModel):
    sound_alarm: bool
