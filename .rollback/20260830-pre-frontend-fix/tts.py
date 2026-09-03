"""Piper TTS 客户端 — Wyoming 协议。

Wyoming 协议格式:
  1. 每条消息是一行 JSON header，后跟可选的 data_bytes 和 payload_bytes
  2. JSON header: {"type":"事件名","version":"1.10.0","data_length":N,"payload_length":M}
  3. 如果 data_length>0，header 后紧跟 N 字节的 JSON data（UTF-8）
  4. 如果 payload_length>0，data 后紧跟 M 字节的二进制 payload

合成流程:
  client -> {"type":"describe"}\n
  server -> {"type":"info","data_length":N}\n + N bytes JSON
  client -> {"type":"synthesize","data_length":N}\n + N bytes JSON({"text":"...","voice":{"id":"..."}})
  server -> {"type":"audio-start","data_length":N}\n + data (音频格式信息)
  server -> {"type":"audio-chunk","data_length":N,"payload_length":M}\n + data + M bytes PCM  (可能多段)
  server -> {"type":"audio-stop"}\n

音频格式为 raw PCM: 22050Hz mono 16-bit signed-integer LE (Piper 默认)
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import wave
from typing import Optional

log = logging.getLogger("envmon.tts")

TTS_HOST = os.environ.get("TTS_HOST", "piper")
TTS_PORT = int(os.environ.get("TTS_PORT", "10200"))
TTS_VOICE = os.environ.get("TTS_VOICE", "zh_CN-huayan-medium")
TTS_ENABLED = os.environ.get("TTS_ENABLED", "1") == "1"

_WYOMING_VERSION = "1.10.0"


def is_enabled() -> bool:
    """TTS 功能是否启用。"""
    return TTS_ENABLED


async def _read_wyoming_message(reader: asyncio.StreamReader) -> Optional[dict]:
    """读取一条 Wyoming 协议消息，返回 {type, data, payload}。"""
    # 读 JSON header 行
    header_line = await reader.readline()
    if not header_line:
        return None
    header_line = header_line.decode("utf-8", "replace").strip()
    if not header_line:
        return None
    try:
        header = json.loads(header_line)
    except json.JSONDecodeError:
        log.warning("Wyoming header 解析失败: %s", header_line[:200])
        return None

    msg_type = header.get("type", "")
    data_length = header.get("data_length", 0) or 0
    payload_length = header.get("payload_length", 0) or 0

    # 读 data（JSON 块）
    data = {}
    if data_length > 0:
        data_bytes = await reader.readexactly(data_length)
        try:
            data = json.loads(data_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}

    # 读 payload（二进制）
    payload = b""
    if payload_length > 0:
        payload = await reader.readexactly(payload_length)

    return {"type": msg_type, "data": data, "payload": payload}


def _write_wyoming_message(msg_type: str, data: Optional[dict] = None,
                           payload: Optional[bytes] = None) -> bytes:
    """构造一条 Wyoming 协议消息的字节流。"""
    header: dict = {"type": msg_type, "version": _WYOMING_VERSION}

    data_bytes = b""
    if data:
        data_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        header["data_length"] = len(data_bytes)

    if payload:
        header["payload_length"] = len(payload)

    header_json = json.dumps(header, ensure_ascii=False)
    msg = header_json.encode("utf-8") + b"\n"
    if data_bytes:
        msg += data_bytes
    if payload:
        msg += payload
    return msg


async def _wyoming_synthesize(text: str, voice_id: Optional[str] = None) -> bytes:
    """通过 Wyoming 协议向 Piper 请求 TTS 合成，返回 WAV 字节流。"""
    voice = voice_id or TTS_VOICE
    reader, writer = await asyncio.open_connection(TTS_HOST, TTS_PORT)

    try:
        # Step 1: 发送 describe 请求
        writer.write(_write_wyoming_message("describe"))
        await writer.drain()

        # 读取 info 响应（可能收到多行，取 info 类型的）
        while True:
            msg = await _read_wyoming_message(reader)
            if msg is None:
                raise ConnectionError("Piper 连接断开，未收到 info 响应")
            if msg["type"] == "info":
                log.debug("Piper info received, voices available")
                break
            # 忽略其他消息

        # Step 2: 发送 synthesize 请求
        synth_data = {
            "text": text,
            "voice": {"id": voice},
        }
        writer.write(_write_wyoming_message("synthesize", data=synth_data))
        await writer.drain()

        # Step 3: 接收音频数据
        audio_chunks = []
        sample_rate = 22050  # Piper 默认
        sample_width = 2     # 16-bit
        channels = 1         # mono

        while True:
            msg = await _read_wyoming_message(reader)
            if msg is None:
                break

            msg_type = msg["type"]

            if msg_type == "audio-start":
                # 音频格式信息
                fmt = msg.get("data", {})
                sample_rate = fmt.get("rate", sample_rate)
                sample_width = fmt.get("width", sample_width)
                channels = fmt.get("channels", channels)
                log.debug("audio-start: rate=%d width=%d ch=%d",
                          sample_rate, sample_width, channels)

            elif msg_type == "audio-chunk":
                # PCM 音频数据
                if msg["payload"]:
                    audio_chunks.append(msg["payload"])

            elif msg_type == "audio-stop":
                # 合成结束
                break

            elif msg_type == "error":
                error_msg = msg.get("data", {}).get("message", "unknown")
                raise RuntimeError(f"Piper TTS 错误: {error_msg}")

            # 忽略其他事件

        if not audio_chunks:
            raise RuntimeError("Piper 未返回音频数据")

        raw_pcm = b"".join(audio_chunks)

        # 包装成 WAV
        return _pcm_to_wav(raw_pcm, sample_rate, sample_width, channels)

    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 22050,
                sample_width: int = 2, channels: int = 1) -> bytes:
    """将 raw PCM 数据包装成 WAV 格式字节流。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


async def synthesize(text: str, voice_id: Optional[str] = None) -> bytes:
    """将文本合成为语音，返回 WAV 字节流。

    Args:
        text: 要合成的中文文本
        voice_id: 可选语音模型 ID（默认从环境变量读取）

    Returns:
        WAV 格式音频字节流
    """
    if not TTS_ENABLED:
        raise RuntimeError("TTS 功能未启用 (TTS_ENABLED=0)")

    text = text.strip()
    if not text:
        raise ValueError("合成文本不能为空")

    # 截断超长文本
    if len(text) > 500:
        text = text[:500]
        log.warning("文本过长，已截断至 500 字符")

    return await _wyoming_synthesize(text, voice_id)


def synthesize_sync(text: str, voice_id: Optional[str] = None) -> bytes:
    """同步版本的文本合成（在线程池中调用）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(synthesize(text, voice_id))
    finally:
        loop.close()


def build_alarm_text(device_id: str, level: int, reason: str,
                     patient_name: Optional[str] = None) -> str:
    """根据报警信息构造语音播报文本。"""
    if level == 0:
        if patient_name:
            return f"{patient_name}，各项指标已恢复正常。"
        return f"设备{device_id}，报警已解除。"
    elif level == 1:
        prefix = f"{patient_name}，" if patient_name else f"设备{device_id}，"
        return f"{prefix}请注意，{reason}。"
    else:  # level == 2
        prefix = f"{patient_name}，" if patient_name else f"设备{device_id}，"
        return f"{prefix}紧急报警，{reason}。请立即处理。"
