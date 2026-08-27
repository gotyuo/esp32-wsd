#include "ui.h"
#include "pins.h"
#include "st7735.h"

static ST7735 tft;

bool DisplayUI::begin() {
    pinMode(PIN_TFT_BL, OUTPUT);
    digitalWrite(PIN_TFT_BL, HIGH);
    delay(50);
    tft.begin(PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST, PIN_TFT_MOSI, PIN_TFT_SCK);
    return true;
}

void DisplayUI::backlight(bool on) {
    digitalWrite(PIN_TFT_BL, on ? HIGH : LOW);
}

// 启动画面（横屏 160x80）
void DisplayUI::showBoot(const char *fw_ver) {
    tft.fillScreen(C_BLACK);
    tft.setTextColor(C_WHITE);
    tft.setTextSize(2);
    // "ENVMON" 6字×12px=72px 水平居中
    tft.setCursor((160 - 72) / 2, 22);
    tft.print("ENVMON");
    tft.setTextSize(1);
    tft.setTextColor(C_GRAY);
    // "FW x.y.z" 水平居中
    char line[24];
    snprintf(line, sizeof(line), "FW %s", fw_ver);
    tft.setCursor((160 - 6 * (int)strlen(line)) / 2, 46);
    tft.print(line);
}

uint16_t DisplayUI::colorForNet(NetState net) {
    switch (net) {
        case NET_MQTT_OK:    return C_GREEN;
        case NET_WIFI_OK:    return C_ORANGE;
        case NET_CONNECTING: return C_YELLOW;
        default:             return C_RED;
    }
}

const char *DisplayUI::netLabel(NetState net) {
    switch (net) {
        case NET_MQTT_OK:    return "MQTT";
        case NET_WIFI_OK:    return "WIFI";
        case NET_CONNECTING: return "CONN";
        default:             return "OFF";
    }
}

// 顶部状态栏（横屏，宽160 高12）
void DisplayUI::drawStatusBar(NetState net, bool alarm) {
    tft.fillRect(0, 0, 160, 12, C_BLACK);
    // 网络状态圆点
    tft.fillCircle(6, 6, 3, colorForNet(net));
    tft.setTextSize(1);
    tft.setTextColor(colorForNet(net));
    tft.setCursor(13, 3);
    tft.print(netLabel(net));
    if (alarm) {
        tft.setTextColor(C_RED);
        tft.setCursor(124, 3);
        tft.print("ALARM");
    }
}

// 主界面（横屏 160x80）：左侧大号温度，右上湿度，右下气压
void DisplayUI::showMain(const EnvData &d, NetState net, bool alarm) {
    if (_apMode) { _apMode = false; tft.fillScreen(C_BLACK);
                   _lastTemp[0] = _lastHum[0] = _lastPres[0] = 0; }

    if (net != _lastNet || alarm != _lastAlarm) {
        drawStatusBar(net, alarm);
        _lastNet = net; _lastAlarm = alarm;
    }

    char buf[16];

    // ---- 温度（左侧大号） ----
    if (isnan(d.temp_c)) snprintf(buf, sizeof(buf), "--.-");
    else                 snprintf(buf, sizeof(buf), "%.1f", d.temp_c);
    if (strcmp(buf, _lastTemp) != 0) {
        tft.fillRect(2, 20, 82, 32, C_BLACK);
        uint8_t sz = (strlen(buf) >= 5) ? 2 : 3;   // 太长自动缩小
        tft.setTextColor(C_ORANGE);
        tft.setTextSize(sz);
        tft.setCursor(4, 26);
        tft.print(buf);
        tft.setTextSize(1);                        // 单位 C
        tft.setCursor(4 + 6 * sz * strlen(buf) + 2, 28);
        tft.print("C");
        strcpy(_lastTemp, buf);
    }

    // ---- 湿度（右上） ----
    if (isnan(d.hum_pct)) snprintf(buf, sizeof(buf), "--.-%");
    else                  snprintf(buf, sizeof(buf), "%.1f%%", d.hum_pct);
    if (strcmp(buf, _lastHum) != 0) {
        tft.fillRect(86, 14, 72, 18, C_BLACK);
        tft.setTextColor(C_CYAN);
        tft.setTextSize(2);
        tft.setCursor(86, 16);
        tft.print(buf);
        strcpy(_lastHum, buf);
    }

    // ---- 气压（右下，数字大号 + hPa 小号） ----
    if (isnan(d.pres_hpa)) snprintf(buf, sizeof(buf), "----");
    else                   snprintf(buf, sizeof(buf), "%.0f", d.pres_hpa);
    if (strcmp(buf, _lastPres) != 0) {
        tft.fillRect(86, 44, 72, 20, C_BLACK);
        tft.setTextColor(C_YELLOW);
        tft.setTextSize(2);
        tft.setCursor(86, 48);
        tft.print(buf);
        tft.setTextSize(1);
        tft.setCursor(86 + 6 * 2 * strlen(buf) + 3, 54);
        tft.print("hPa");
        strcpy(_lastPres, buf);
    }
}

// 配网 AP 界面（横屏）- 翻页模式，每页5秒，大字显示
void DisplayUI::showAP(const char *ap_ssid, const EnvData *d, bool voiceTrig) {
    if (!_apMode) {
        tft.fillScreen(C_BLACK);
        _apMode = true;
        _lastTemp[0] = _lastHum[0] = _lastPres[0] = 0;
        _apPage = 0;
        _apLastSwitch = 0;
    }
    uint32_t now = millis();
    // 语音触发：立即翻页
    if (voiceTrig) {
        _apLastSwitch = now - 5000;  // 强制触发翻页
    }
    if (now - _apLastSwitch < 5000) return;  // 每页5秒
    _apLastSwitch = now;
    _apPage = (_apPage + 1) % 6;  // 6页循环

    tft.fillScreen(C_BLACK);

    switch (_apPage) {
        case 0:  // CONFIG MODE
            tft.setTextSize(3);
            tft.setTextColor(C_YELLOW);
            tft.setCursor(4, 10);
            tft.print("CONFIG");
            tft.setCursor(4, 38);
            tft.print("MODE");
            break;
        case 1:  // AP 名称
            tft.setTextSize(2);
            tft.setTextColor(C_WHITE);
            tft.setCursor(4, 4);
            tft.print("AP Name:");
            tft.setTextSize(2);
            tft.setTextColor(C_CYAN);
            tft.setCursor(4, 26);
            tft.print(ap_ssid);
            break;
        case 2:  // 浏览器打开
            tft.setTextSize(2);
            tft.setTextColor(C_WHITE);
            tft.setCursor(4, 2);
            tft.print("Browser:");
            tft.setTextSize(2);
            tft.setTextColor(C_GREEN);
            tft.setCursor(4, 26);
            tft.print("192.168.4.1");
            break;
        case 3:  // 温度
            if (d && d->valid) {
                char buf[16];
                tft.drawChinese(4, 2, CN_WEN, C_ORANGE);   // 温
                tft.drawChinese(22, 2, CN_DU, C_ORANGE);   // 度
                tft.setTextSize(4);
                snprintf(buf, sizeof(buf), "%.1f", d->temp_c);
                tft.setCursor(4, 24);
                tft.print(buf);
                tft.setTextSize(2);
                tft.setCursor(4 + 6*4*strlen(buf) + 4, 30);
                tft.print("C");
            } else {
                tft.setTextSize(2);
                tft.setTextColor(C_RED);
                tft.setCursor(4, 26);
                tft.print("No Sensor");
            }
            break;
        case 4:  // 湿度
            if (d && d->valid) {
                char buf[16];
                tft.drawChinese(4, 2, CN_SHI, C_CYAN);   // 湿
                tft.drawChinese(22, 2, CN_DU, C_CYAN);   // 度
                tft.setTextSize(4);
                snprintf(buf, sizeof(buf), "%.1f", d->hum_pct);
                tft.setCursor(4, 24);
                tft.print(buf);
                tft.setTextSize(2);
                tft.setCursor(4 + 6*4*strlen(buf) + 4, 30);
                tft.print("%");
            } else {
                tft.setTextSize(2);
                tft.setTextColor(C_RED);
                tft.setCursor(4, 26);
                tft.print("No Sensor");
            }
            break;
        case 5:  // 气压
            if (d && d->valid) {
                char buf[16];
                tft.drawChinese(4, 2, CN_QI, C_YELLOW);   // 气
                tft.drawChinese(22, 2, CN_YA, C_YELLOW);  // 压
                tft.setTextSize(4);
                snprintf(buf, sizeof(buf), "%.0f", d->pres_hpa);
                tft.setCursor(4, 24);
                tft.print(buf);
                tft.setTextSize(2);
                tft.setCursor(4 + 6*4*strlen(buf) + 4, 30);
                tft.print("hPa");
            } else {
                tft.setTextSize(2);
                tft.setTextColor(C_RED);
                tft.setCursor(4, 26);
                tft.print("No Sensor");
            }
            break;
    }
}

#ifdef DISPLAY_DIAG
// ============================================================
// 显示诊断 v1.5.2：GC9109 + 批量 drawChar 修复
//   v1.5.1 结果：GC9109 init + 0x68 + (0,24) → 红色边框贴边✓ 但文字乱码✗
//   根因分析：fillRect(大块) 正常，drawPixel(逐像素) 乱码
//     = GC9109 无法处理快速 1x1 setAddrWindow 序列，地址指针混乱
//   v1.5.2 修复：drawChar 改为批量写入（整字一个地址窗口 + 一次 burst）
//   A  GC9109 + 0x68 + (0,24)   ← 文章精确配置（MX|MV|BGR）
//   B  GC9109 + 0x28 + (0,24)   ← MV|BGR（去掉 MX，若 A 文字镜像则用此）
//   C  GC9109 + 0xE8 + (0,24)   ← MY|MX|MV|BGR
//   D  GC9109 + 0x88 + (0,24)   ← MY|MV|BGR
//   E  GC9109 + 0x48 + (0,24)   ← MX|BGR（MV=0 竖屏）
//   F  ST7735 + 0x68 + (0,24)   ← 对照：ST7735 init + 文章参数
//   G  QUAD GC9109 + 0x68       ← 四色方向验证
//   H  PIXEL GC9109 + 0x68      ← drawPixel 逐像素测试（对照，应仍乱码）
// 每个 FONT 阶段：2px 红色边框探针 + 文字。
// 阶段 H：用 drawPixel 画 3 条水平线（确认逐像素确实乱码）。
// 文字可正常横排阅读 = 修复成功。每阶段 10 秒。串口输入 A-H 跳转。
// ============================================================
enum DiagKind { K_QUAD, K_FONT };
struct DiagStage {
    char id;
    const char *name;
    uint8_t madctl;
    int16_t offx, offy;
    bool inv;
    bool gc9109;
    DiagKind kind;
};
static const DiagStage kStages[] = {
    {'A', "GC9109 0x68 (0,24)", 0x68, 0, 24, false, true,  K_FONT},
    {'B', "GC9109 0x68 (0,0)",  0x68, 0, 0,  false, true,  K_FONT},
    {'C', "GC9109 0x68 (24,1)", 0x68, 24, 1, false, true,  K_FONT},
    {'D', "GC9109 0xE8 (0,24)", 0xE8, 0, 24, false, true,  K_FONT},
    {'E', "GC9109 0xA8 (0,24)", 0xA8, 0, 24, false, true,  K_FONT},
    {'F', "GC9109 0x48 (0,24)", 0x48, 0, 24, false, true,  K_FONT},
    {'G', "ST7735 0x68 (0,24)", 0x68, 0, 24, false, false, K_FONT},
    {'H', "QUAD GC9109 0x68",   0x68, 0, 24, false, true,  K_QUAD},
};
static const int kStageCount = (int)(sizeof(kStages) / sizeof(kStages[0]));

// 2px 红色边框探针：偏移正确时紧贴屏幕四边
static void drawBorder(int16_t W, int16_t H) {
    tft.fillRect(0, 0, W, 2, C_RED);
    tft.fillRect(0, H - 2, W, 2, C_RED);
    tft.fillRect(0, 0, 2, H, C_RED);
    tft.fillRect(W - 2, 0, 2, H, C_RED);
}

static void drawDiagStage(const DiagStage &s, int16_t W, int16_t H) {
    if (s.kind == K_QUAD) {
        tft.fillRect(0, 0, W / 2, H / 2, C_RED);
        tft.fillRect(W / 2, 0, W / 2, H / 2, C_GREEN);
        tft.fillRect(0, H / 2, W / 2, H / 2, C_BLUE);
        tft.fillRect(W / 2, H / 2, W / 2, H / 2, C_YELLOW);
        drawBorder(W, H);   // 边框覆盖在色块之上，便于观察偏移
        return;
    }
    tft.fillScreen(C_BLACK);
    drawBorder(W, H);
    tft.setTextColor(C_WHITE);
    tft.setTextSize(2);
    tft.setCursor(6, 10);
    tft.print("ENVMON");
    tft.setTextSize(1);
    tft.setCursor(6, 34);
    tft.print("0123456789");
    tft.setCursor(6, 46);
    tft.print("ABCDEFGHIJ");
    tft.setCursor(6, 58);
    tft.print("abcdefghij");
    tft.setCursor(6, 70);
    tft.print("FW 1.5.1-diag");
}

void DisplayUI::diagLoop() {
    int idx = 0;
    for (;;) {
        const DiagStage &s = kStages[idx];
        if (s.gc9109)
            tft.initGC9109(s.madctl, s.offx, s.offy, 160, 80);
        else
            tft.fullInit(s.madctl, s.offx, s.offy, 160, 80);
        tft.setInversion(s.inv);
        const int16_t W = tft.width(), H = tft.height();
        drawDiagStage(s, W, H);
        Serial.printf("[DIAG] stage %c %s MADCTL=0x%02X off=(%d,%d)%s %dx%d start\n",
                      s.id, s.name, s.madctl, s.offx, s.offy,
                      s.inv ? " INVON" : "", W, H);
        const uint32_t t0 = millis();
        bool jumped = false;
        while (millis() - t0 < 10000) {
            if (Serial.available()) {
                String str = Serial.readStringUntil('\n');
                str.trim();
                if (str.length() == 1) {
                    char c = str[0];
                    if (c >= '1' && c <= '8')      c = 'A' + (c - '1');
                    else if (c == '0')             c = 'H';
                    else if (c >= 'a')             c -= 32;
                    if (c >= 'A' && c <= 'H') {
                        idx = c - 'A';
                        jumped = true;
                        break;
                    }
                }
            }
            uint32_t remain = 10 - (millis() - t0) / 1000;
            Serial.printf("[DIAG] stage %c %s MADCTL=0x%02X off=(%d,%d)%s t=%lu\n",
                          s.id, s.name, s.madctl, s.offx, s.offy,
                          s.inv ? " INVON" : "", remain);
            delay(950);
        }
        if (!jumped) idx = (idx + 1) % kStageCount;
    }
}
#endif  // DISPLAY_DIAG

// ---------------- TTS 语音播报显示 ----------------
void DisplayUI::showTtsMessage(const String &text) {
    // 在屏幕中央显示 TTS 播报文本，3 秒后由 showMain 调用自然覆盖
    _ttsShowUntil = millis() + 3000;

    // 半屏黑底白字，2 行
    tft.fillScreen(C_BLACK);
    tft.setTextColor(C_YELLOW);
    tft.setTextSize(1);
    tft.setCursor(2, 4);
    tft.print("[TTS]");

    tft.setTextColor(C_WHITE);
    tft.setTextSize(1);
    // 自动换行：160px 宽，每行约 20 个 ASCII 字符或 10 个中文字符
    // 中文用 font_cn.h 的 drawChinese，这里简单用 ASCII 方式
    int y = 20;
    int x = 2;
    for (size_t i = 0; i < text.length() && y < 76; i++) {
        char c = text.charAt(i);
        if (c == '\n') { x = 2; y += 10; continue; }
        if (x > 150) { x = 2; y += 10; }
        tft.setCursor(x, y);
        tft.print(c);
        x += 6;
    }

    // 喇叭图标（简单 ASCII）
    tft.setTextColor(C_CYAN);
    tft.setCursor(140, 4);
    tft.print(")");
}
