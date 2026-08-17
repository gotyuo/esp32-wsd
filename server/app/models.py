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


class RegisterDeviceIn(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=32)
    name: str = Field(default="", max_length=64)


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


# ================================================================ ICU 重症监护
class PatientCreate(BaseModel):
    pid: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field("", max_length=32)
    gender: str = Field("", pattern=r"^(M|F|)$")
    age: int = Field(0, ge=0, le=150)
    bed_no: str = Field("", max_length=16)
    admit_ts: str = Field("", max_length=32)
    diagnosis: str = Field("", max_length=512)
    doctor: str = Field("", max_length=64)
    phone: str = Field("", max_length=20)


class PatientUpdate(BaseModel):
    name: str = Field("", max_length=32)
    gender: str = Field("", pattern=r"^(M|F|)$")
    age: int = Field(0, ge=0, le=150)
    bed_no: str = Field("", max_length=16)
    diagnosis: str = Field("", max_length=512)
    doctor: str = Field("", max_length=64)
    phone: str = Field("", max_length=20)


class LinkDeviceIn(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=32)
    role: str = Field("primary", pattern=r"^(primary|secondary)$")


class VitalIn(BaseModel):
    source: str = Field("esp32", pattern=r"^(esp32|his|ecg|ventilator|lab|manual)$")
    source_device: str = Field("", max_length=32)
    ts: str = Field("", max_length=32)
    sp_o2: Optional[float] = None
    pr_hr: Optional[float] = None
    ecg_hr: Optional[float] = None
    ecg_st: Optional[float] = None
    rr_bpm: Optional[float] = None
    etco2: Optional[float] = None
    sbp: Optional[float] = None
    dbp: Optional[float] = None
    map_bp: Optional[float] = None
    ibp: Optional[float] = None
    temp_c: Optional[float] = None
    glucose: Optional[float] = None
    hum_pct: Optional[float] = None
    pres_hpa: Optional[float] = None
    k_mmol: Optional[float] = None
    na_mmol: Optional[float] = None
    cl_mmol: Optional[float] = None
    ca_mmol: Optional[float] = None
    glucose_lab: Optional[float] = None
    lactate: Optional[float] = None
    ph: Optional[float] = None
    pco2: Optional[float] = None
    po2: Optional[float] = None
    hco3: Optional[float] = None
    be: Optional[float] = None
    extra: str = Field("", max_length=1024)


class OrderIn(BaseModel):
    source: str = Field("his", pattern=r"^(his|manual|lis)$")
    order_no: str = Field("", max_length=32)
    drug_name: str = Field("", max_length=128)
    dosage: str = Field("", max_length=64)
    route: str = Field("", pattern=r"^(iv|im|sc|oral|pump|po|ivg|ivgtt|)$")
    start_ts: str = Field("", max_length=32)
    end_ts: str = Field("", max_length=32)
    rate_mlph: Optional[float] = None
    operator: str = Field("", max_length=64)


class LabResultIn(BaseModel):
    source: str = Field("lis", pattern=r"^(lis|blood_gas|manual)$")
    item_code: str = Field("", max_length=20)
    item_name: str = Field("", max_length=64)
    value: Optional[float] = None
    unit: str = Field("", max_length=16)
    ref_min: Optional[float] = None
    ref_max: Optional[float] = None
    result_ts: str = Field("", max_length=32)
    critical: bool = False
