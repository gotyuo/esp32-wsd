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
import re
import wave
from typing import Optional

import threading
from collections import OrderedDict

log = logging.getLogger("envmon.tts")

TTS_HOST = os.environ.get("TTS_HOST", "piper")
TTS_PORT = int(os.environ.get("TTS_PORT", "10200"))
TTS_VOICE = os.environ.get("TTS_VOICE", "zh_CN-huayan-medium")
TTS_ENABLED = os.environ.get("TTS_ENABLED", "1") == "1"

# WAV 合成结果缓存：(text, voice) -> WAV bytes。
# 报警持续越限时同一句会被反复请求，命中缓存省掉 Piper 合成开销。
_TTS_CACHE_MAX = int(os.environ.get("TTS_CACHE_MAX", "32"))
_tts_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_tts_cache_lock = threading.Lock()

# 报警语音文本的最大字符数。固件 TTS 缓冲 128KB ≈ 2.97 秒语音。
# 实测（Piper zh_CN-huayan-medium，含标点停顿）同长度差异很大：
#   19 字可低至 119852B 也可高达 132140B（超 128KB），方差来自不同字的时长。
# 固定字符数无法精确控制体积，故取 18 字作为保守上限。不可再往上加。
# 可通过 TTS_MAX_CHARS 环境变量调整（风险自负）。
TTS_MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "18"))

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

    带进程内缓存：报警会周期重复下发同一文本（如持续越限），
    反复合成同一句是浪费 CPU 和 Piper 吞吐，命中缓存直接返回。

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

    voice = voice_id or TTS_VOICE
    key = (text, voice)

    # 缓存命中：同一文本+语音直接返回，避免重复合成
    _tts_cache_lock.acquire()
    try:
        if key in _tts_cache:
            _tts_cache.move_to_end(key)
            log.debug("TTS cache hit: %s", text[:30])
            return _tts_cache[key]
    finally:
        _tts_cache_lock.release()

    wav_data = await _wyoming_synthesize(text, voice)

    _tts_cache_lock.acquire()
    try:
        _tts_cache[key] = wav_data
        _tts_cache.move_to_end(key)
        # 超过条数上限时逐出最久未用的
        while len(_tts_cache) > _TTS_CACHE_MAX:
            _tts_cache.popitem(last=False)
    finally:
        _tts_cache_lock.release()

    return wav_data


def synthesize_sync(text: str, voice_id: Optional[str] = None) -> bytes:
    """同步版本的文本合成（在线程池中调用）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(synthesize(text, voice_id))
    finally:
        loop.close()


_NUM_THRESHOLD_RE = re.compile(
    r"(\S*?)(\s*-?\d+(?:\.\d+)?\s*)([^;\s\[\]]+)\s*\[(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\]"
)


def alarm_reason_for_speech(reason: str) -> str:
    """把报警原因精简成适合语音播报的形式。

    check_alarm 生成的原因是给人看的屏幕文本，带数值和阈值区间
    （如「温度 38.5 超出 [5.0, 40.0]」），对语音播报完全冗余却占满
    128KB 缓冲预算，导致最终读出来是「血氧低于阈」这类半截词。
    语音里只保留「指标名 + 方向」，数值和阈值在屏幕上才看得清。

    方向由数值与区间比较得出（而非靠「超出/低于」字面推断，因为「超出」
    本身不含方向），这样「温度 38.5 超出 [5, 40]」能正确读作「温度过高」。

    例：「温度 38.5 超出 [5, 40]」->「温度过高」
        「湿度 5.0 低于 [20, 90]」->「湿度过低」
        「温度 38.5 接近边界 [5, 40]」->「温度接近阈值」
        「温度 38.5 超出 [5, 40]; 湿度 5.0 低于 [20, 90]」->「温度过高，湿度过低」
    无法识别的原文按中文标点分段取前 4 段。
    """
    if not reason or not reason.strip():
        return ""

    parts_urgent, parts_warn = [], []
    for seg in reason.split(";"):
        seg = seg.strip()
        if not seg:
            continue
        m = _NUM_THRESHOLD_RE.search(seg)
        if not m:
            continue
        metric = m.group(1).strip()
        word = m.group(3).strip()
        try:
            val = float(m.group(2))
            lo = float(m.group(4))
            hi = float(m.group(5))
        except ValueError:
            parts_urgent.append(f"{metric}异常")
            continue

        if "接近" in word:
            # 未越界，仅接近阈值——放后面，避免挤掉真正的超界指标
            parts_warn.append(f"{metric}接近阈值")
        elif val > hi:
            parts_urgent.append(f"{metric}过高")
        elif val < lo:
            parts_urgent.append(f"{metric}过低")
        else:
            parts_urgent.append(f"{metric}异常")

    # 超界（紧急）在前、接近阈值（预警）在后：字符预算不足时裁掉的是预警项，
    # 不会把真正越界的指标裁掉——ICU 场景下越界信息优先于临界提醒。
    parts = parts_urgent + parts_warn

    if not parts:
        # 兜底：无结构化数值时按中文标点分段取前几段，避免丢内容
        segs = [s for s in re.split(r"[，。；、,;]", reason) if s.strip()]
        return "".join(s.strip() for s in segs[:4])
    return "，".join(parts)


def _device_label(device_id: str) -> str:
    """取设备 ID 末位作为短播报标签，如 envmon-a1b2c3 -> 3号设备。

    完整设备 ID 形如 envmon-a1b2c3（12 字符），直接读入语音会占满 128KB 缓冲
    预算，导致报警原因被截断掉、播报无意义。取末 3 位数字既能区分设备，
    又不浪费字符预算。ID 含字母或异常时回退为 设备1号。
    """
    tail = device_id[-3:] if len(device_id) >= 3 else device_id
    if tail.isdigit():
        return f"{tail}号设备"
    return "设备1号"


def _trim_middle(head: str, tail: str, middle: str,
                 max_chars: int = TTS_MAX_CHARS) -> str:
    """返回长度不超过 max_chars 的完整句子，句号由本函数统一追加。

    head/tail 是固定框架（含行动指令等不可丢的信息），middle 是可变的报警原因。
    预算不足时只裁 middle，框架优先保留——受固件 128KB 缓冲限制，长语音尾部
    会被丢弃，所以关键信息必须在框架里而非末尾。

    注意：句号只在本函数末尾追加一次，调用方不要再加，否则产生"。。"且
    超出 max_chars 预算。
    """
    middle = middle.strip().rstrip("，。、；：")

    def _build(m: str) -> str:
        s = head + m + tail
        # 避免与 tail 已有标点重复
        return s.rstrip("。") + "。"

    if len(_build(middle)) <= max_chars:
        return _build(middle)

    # 框架（含句号）已占用的空间，留给 middle 的预算
    budget = max_chars - len(head) - len(tail.rstrip("。"))
    if budget >= 1:
        return _build(middle[:budget].rstrip("，。、；："))

    # 极端情况：中间无预算，放弃 tail 把预算给 middle
    budget = max_chars - len(head) - 1
    if budget >= 1:
        return head + middle[:budget].rstrip("，。、；：") + "。"
    return head[: max_chars - 1] + "。"


def build_alarm_text(device_id: str, level: int, reason: str,
                     patient_name: Optional[str] = None) -> str:
    """根据报警信息构造语音播报文本。

    受固件 128KB TTS 缓冲限制（实测安全上限 19 字 / 2.86 秒），文案结构为
    「身份 + 固定框架 + 可变原因」，预算不足时只裁原因。紧急级别把行动指令
    放进固定框架（tail），保证即使原因被裁也不丢动作。
    """
    label = _device_label(device_id)
    who = f"{patient_name}，" if patient_name else f"{label}，"

    if level == 0:
        # 恢复提示无需具体原因，句子本身短
        return f"{who}已恢复正常。"

    # 原因精简：原始文本带数值阈值（「温度 38.5 超出 [5, 40]」）对语音冗余，
    # 且会挤掉行动指令。精简成「温度过高」后再受字符预算约束。
    reason = alarm_reason_for_speech(reason).strip() or "指标异常"

    if level == 1:
        return _trim_middle(f"{who}请注意，", "", reason)

    # level == 2：紧急。"请处理"放框架末尾，原因预算不足时只裁原因。
    # 用「请处理」而非「请立即处理」省 2 字符——实测同长度 19 字可高达 132KB
    # 超限，紧急文案必须给体积留余量，动作指令的紧迫性由「紧急：」前缀承担。
    return _trim_middle(f"{who}紧急：", "，请处理。", reason)
