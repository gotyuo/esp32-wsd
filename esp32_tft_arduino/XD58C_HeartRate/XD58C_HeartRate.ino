// ============================================================
//  XD-58C 光学心率脉搏传感器 + 0.96" ST7735S 彩屏 (ESP32-S3)
// ------------------------------------------------------------
//  XD-58C 是**模拟输出**器件（3 根线：+ / - / S），不是 I2C，
//  没有 SCL，也测不了血氧。本程序读它的模拟电压，算出心率 BPM，
//  并在屏上实时画脉搏波形。
//
//  接线：
//    屏(ST7735S)   SCK->GPIO12  MOSI->GPIO11  CS->GPIO10
//                  DC->GPIO7    RST->GPIO6    BL->GPIO5
//                  VCC->3.3V    GND->GND
//    XD-58C        +  -> 3.3V   （⚠ 必须 3.3V，接 5V 输出会打坏 ESP32 的 ADC）
//                  -  -> GND
//                  S  -> GPIO2  （ESP32-S3: GPIO2 = ADC1_CH1，模拟输入；板上丝印就是 "2"，无 D 编号）
//
//  串口 115200：与参考算法一致的 "BPM:xx IBI:xxx"，另有每 2s 一行 [SIG] 诊断。
//
//  ------------------------------------------------------------
//  v1.1 数值准确性改进（详细校验见 README 的「数值校验」章节）：
//   1) 【关键】屏是 bit-bang 软件 SPI：一次 fillRect(0,0,160,32) 约 270ms。
//      v1.0 每 400ms 调一次 → 采样被阻塞 ~67%，期间的搏动最多延迟 270ms 才被识别，
//      IBI 误差可达 ±270ms（800ms 间期会算成 55~109 BPM，数值乱跳）。
//      v1.1 改为**按字段差量重绘**：稳态每次只重画变化的字符（约 2.5~30ms）；
//      整块清屏只在"手指状态切换"时发生一次，而那一刻测量状态已重置，不会污染结果。
//   2) 采样改为严格 2ms 节拍，带追帧与掉帧统计（stall）。
//   3) 峰值判定改双阈值滞回（进 65% / 出 35%），修掉 v1.0 在阈值为负时
//      `above` 无法复位、此后不再识别搏动的隐患。
//   4) IBI 伪迹剔除：与均值偏差 >35% 的间期不计入平均（但作为新的计时起点）。
//   5) 手指状态切换时重置测量，杜绝"跨会话假 IBI"（v1.0 会把抬起前与重新放上后的
//      两个搏动拼成一个间期，算出明显偏慢的 BPM）。
//   6) 至少累计 2 个 IBI 才显示数字（v1.0 单个间期就出数，抖动大）。
//   7) 波形自动量程（v1.0 固定 200 counts，小信号会被画成一条直线）。
//   8) 增加 PPG_INVERT 极性开关与 SERIAL_PLOT 绘图开关，便于实测核对。
// ============================================================
#include <Arduino.h>
#include <string.h>
#include "st7735.h"

// 版本标识：年月日 + 版本号（回退时以此为准）
#define FW_VERSION "20261006-v1.1"

// ---------- 屏引脚 ----------
#define PIN_SCK  12
#define PIN_MOSI 11
#define PIN_CS   10
#define PIN_DC   7
#define PIN_RST  6
#define PIN_BLK  5

// ---------- XD-58C 模拟输入 ----------
// ESP32-S3 映射: GPIO1=CH0, GPIO2=CH1, GPIO3=CH2, GPIO4=CH3
#define PIN_PPG 2

// 若实测波形是"向下"的尖峰（极性与预期相反），把这里改成 1
#define PPG_INVERT 0

// 1 = 以 100Hz 向串口输出 ac 值，可用 Arduino 串口绘图器核对波形极性/幅度。
// ⚠ 仅供接线核对：串口输出会占用采样时间，精度下降；核对完请改回 0。
#define SERIAL_PLOT 0

// ---------- 采样参数 ----------
#define SAMPLE_INTERVAL_US 2000     // 500 Hz，与参考算法要求一致
#define STALL_LIMIT_US     20000    // 单次落后 >20ms 视为掉帧，重新对齐节拍

// ---------- 峰值检测参数 ----------
#define IBI_MIN_MS        300       // 上限 200 BPM
#define IBI_MAX_MS        2000      // 下限 30 BPM
#define IBI_OUTLIER_PCT   35        // 与均值偏差超过 35% 判为伪迹
#define IBI_NEED          2         // 至少累计几个 IBI 才显示数值
// 判定"手指已放上"的最小幅度（ADC counts）。
// 调法：看串口每 2s 的 [SIG] 行 amp= 的值——
//   手指放上后仍显示 "Put finger on" → 信号弱，调小（如 20）
//   没放手指就显示已测到 → 噪声大，调大（如 60）
#define FINGER_AMP_MIN    30
#define HP_ENTER          0.65f     // 进入阈值 = sigMin + amp*0.65
#define HP_EXIT           0.35f     // 退出阈值 = sigMin + amp*0.35
#define WAVE_SCALE_MIN    30        // 波形自动量程下限（counts）

// ---------- 屏上布局 ----------
#define WAVE_W     160
#define WAVE_TOP   34
#define WAVE_H     44               // 34..77
#define CONTENT_Y  12               // 信息区 12..31
#define BPM_X      2
#define BPM_Y      14
#define BPM_W      3                // 3 位数字，size=2 → 36x16
#define LBL_X      40
#define LBL_Y      20
#define IBI_X      64
#define IBI_Y      20
#define IBI_W      10               // "IBI:1234ms"

ST7735 tft;

// ---------- 测量状态 ----------
static int      sigMax = 0;
static int      sigMin = 0;
static float    baseline = 2048.0f;
static bool     above = false;
static bool     fingerOn = false;
static uint32_t lastBeatUs = 0;      // 0 = 尚未取得计时起点
static unsigned long ibiBuf[8];
static uint8_t  ibiIdx = 0;
static uint8_t  ibiCnt = 0;
static float    bpm = 0.0f;
static unsigned long ibiMs = 0;

// ---------- 采样节拍 ----------
static uint32_t nextSampleUs = 0;
static uint32_t stallCount = 0;
static unsigned long lastDbgMs = 0;

// ---------- 诊断用的最近一次采样值 ----------
static int lastRaw = 0;
static int lastAc  = 0;

// ---------- 波形缓冲 ----------
static int16_t  waveY[WAVE_W];
static int16_t  prevY[WAVE_W];
static uint16_t waveIdx = 0;
static bool     wavePrimed = false;

// ---------- UI 状态 ----------
#define UI_NO_FINGER 0
#define UI_MEASURING 1
#define UI_SHOWING   2
static uint8_t uiState = 0xFF;                 // 强制作一次初始绘制
static char    lastBpmStr[BPM_W + 1] = {' ', ' ', ' ', 0};
static char    lastIbiStr[IBI_W + 1] = "          ";
static unsigned long lastUiMs = 0;

// ============================================================
//  测量重置：手指状态变化时调用，避免跨会话拼出假 IBI
// ============================================================
static void resetMeasurement(int rawNow) {
    sigMax = 0;
    sigMin = 0;
    baseline = (float)rawNow;     // 用当前电平做基线，立即收敛
    above = false;
    lastBeatUs = 0;
    ibiIdx = 0;
    ibiCnt = 0;
    bpm = 0.0f;
    ibiMs = 0;
}

// ============================================================
//  IBI 统计
// ============================================================
static unsigned long ibiMean() {
    unsigned long sum = 0;
    for (uint8_t i = 0; i < ibiCnt; i++) sum += ibiBuf[i];
    return ibiCnt ? (sum / ibiCnt) : 0;
}

static void pushIbi(unsigned long dt) {
    ibiBuf[ibiIdx] = dt;
    ibiIdx = (ibiIdx + 1) % 8;
    if (ibiCnt < 8) ibiCnt++;
    unsigned long mean = ibiMean();
    if (mean) bpm = 60000.0f / (float)mean;
}

// 伪迹判定：样本足够时，偏离均值过多的间期不予采信
static bool isOutlier(unsigned long dt) {
    if (ibiCnt < 3) return false;
    unsigned long mean = ibiMean();
    if (!mean) return false;
    unsigned long dev = (dt > mean) ? (dt - mean) : (mean - dt);
    return (dev * 100UL) > (mean * (unsigned long)IBI_OUTLIER_PCT);
}

// ============================================================
//  采样一次 + 峰值检测
// ============================================================
static void sampleOnce() {
    int raw = analogRead(PIN_PPG);
#if PPG_INVERT
    raw = 4095 - raw;
#endif
    lastRaw = raw;

    // 去直流：EMA 基线（alpha=0.01 → 时间常数约 100 采样 = 200ms）
    baseline = baseline * 0.99f + (float)raw * 0.01f;
    int ac = raw - (int)baseline;
    lastAc = ac;

    // 幅度包络（缓慢衰减，用于自适应阈值与波形量程）
    if (ac > sigMax) sigMax = ac; else sigMax = (int)(sigMax * 0.995f);
    if (ac < sigMin) sigMin = ac; else sigMin = (int)(sigMin * 0.995f);
    int amp = sigMax - sigMin;

    // 手指检测；状态切换时重置测量，本采样不参与判定
    bool finger = (amp > FINGER_AMP_MIN);
    bool justReset = false;
    if (finger != fingerOn) {
        fingerOn = finger;
        resetMeasurement(raw);
        justReset = true;
        amp = 0;
    }

    // 双阈值滞回：进入用高阈值，退出用低阈值
    int hi = sigMin + (int)(amp * HP_ENTER);
    int lo = sigMin + (int)(amp * HP_EXIT);

    if (!justReset) {
        uint32_t nowUs = micros();
        if (!above && fingerOn && ac > hi) {
            above = true;
            if (lastBeatUs == 0) {
                lastBeatUs = nowUs;                  // 第一个搏动只作为计时起点
            } else {
                // 无符号相减，天然抗 micros() 回绕
                unsigned long dt = (unsigned long)((nowUs - lastBeatUs) / 1000UL);
                if (dt > (unsigned long)IBI_MAX_MS) {
                    lastBeatUs = nowUs;              // 间隔过长：重新起算，不记录
                } else if (dt >= (unsigned long)IBI_MIN_MS) {
                    if (isOutlier(dt)) {
                        lastBeatUs = nowUs;          // 伪迹：不计入平均，但重新计时
                    } else {
                        pushIbi(dt);
                        ibiMs = dt;
                        lastBeatUs = nowUs;
                        Serial.printf("BPM:%d IBI:%lu\n", (int)(bpm + 0.5f), ibiMs);
                    }
                }
                // dt < IBI_MIN_MS：疑似抖动，忽略且不更新起点
            }
        } else if (above && ac < lo) {
            above = false;
        }
    }

    // 波形（自动量程，ac = 0 居中）
    int scale = (amp > WAVE_SCALE_MIN) ? amp : WAVE_SCALE_MIN;
    long y = WAVE_TOP + WAVE_H / 2 - (long)ac * (WAVE_H / 2) / scale;
    if (y < WAVE_TOP) y = WAVE_TOP;
    if (y > WAVE_TOP + WAVE_H - 1) y = WAVE_TOP + WAVE_H - 1;
    waveY[waveIdx] = (int16_t)y;

#if SERIAL_PLOT
    static uint8_t dec = 0;
    if (++dec >= 5) { dec = 0; Serial.println(ac); }    // 100Hz
#endif
}

// ============================================================
//  波形绘制（增量：擦旧点 + 画新点；每采样 1 像素）
// ============================================================
static void drawWaveStep() {
    if (!wavePrimed) {
        for (uint16_t i = 0; i < WAVE_W; i++) prevY[i] = -1;
        wavePrimed = true;
    }
    uint16_t x = waveIdx;
    if (prevY[x] >= 0 && prevY[x] != waveY[x]) {
        tft.drawPixel(x, prevY[x], C_BLACK);
    }
    tft.drawPixel(x, waveY[x], C_GREEN);
    prevY[x] = waveY[x];
    waveIdx = (waveIdx + 1) % WAVE_W;
}

// ============================================================
//  UI：按字段差量重绘，单次调用绘制量有上限
// ============================================================
static void drawField(int16_t x, int16_t y, const char *s, uint8_t size,
                      uint16_t color, char *last, uint8_t w, uint8_t maxPerCall) {
    uint8_t drawn = 0;
    for (uint8_t i = 0; i < w && drawn < maxPerCall; i++) {
        char c = s[i] ? s[i] : ' ';
        if (last[i] == c) continue;
        tft.drawChar(x + (int16_t)i * 6 * size, y, c, color, size);
        last[i] = c;
        drawn++;
    }
    last[w] = 0;
}

// 强制下一轮重画（用不可能出现的字符填充比较缓存）
static void forceFields() {
    memset(lastBpmStr, 0x01, BPM_W); lastBpmStr[BPM_W] = 0;
    memset(lastIbiStr, 0x01, IBI_W); lastIbiStr[IBI_W] = 0;
}

static void drawFields() {
    char b[BPM_W + 1];
    snprintf(b, sizeof(b), "%3d", (int)(bpm + 0.5f));          // 右对齐 3 位
    drawField(BPM_X, BPM_Y, b, 2, C_WHITE, lastBpmStr, BPM_W, 3);

    char ib[IBI_W + 1];
    snprintf(ib, sizeof(ib), "IBI:%4lums", ibiMs);
    drawField(IBI_X, IBI_Y, ib, 1, C_GRAY, lastIbiStr, IBI_W, IBI_W);
}

static void drawUi() {
    uint8_t want;
    if (!fingerOn)                         want = UI_NO_FINGER;
    else if (ibiCnt < IBI_NEED)            want = UI_MEASURING;
    else                                   want = UI_SHOWING;

    if (want != uiState) {
        bool fingerStateChanged = (want == UI_NO_FINGER) || (uiState == UI_NO_FINGER);
        if (fingerStateChanged) {
            // 手指状态切换才整块清屏；此刻测量已重置，不会影响结果
            tft.fillRect(0, CONTENT_Y, 160, 20, C_BLACK);
        }
        // 无论哪种切换，都强制重画字段：
        //  - 手指切换时擦掉旧数字；
        //  - 1→2 时用 "IBI:xxxxms" 覆盖 "measuring" 提示（两者都在 IBI 字段槽内）
        forceFields();
        uiState = want;

        tft.setTextSize(1);
        if (want == UI_NO_FINGER) {
            tft.setTextColor(C_YELLOW);
            tft.setCursor(2, 20);
            tft.print("Put finger on");
        } else if (want == UI_MEASURING) {
            tft.setTextColor(C_GRAY);
            tft.setCursor(IBI_X, IBI_Y);
            tft.print("measuring");
        } else {
            tft.setTextColor(C_CYAN);
            tft.setCursor(LBL_X, LBL_Y);
            tft.print("bpm");
        }
        return;                        // 状态文字本轮画完；数字下一轮画
    }

    if (uiState == UI_SHOWING) drawFields();
}

// ============================================================
void setup() {
    Serial.begin(115200);
    delay(200);

    pinMode(PIN_BLK, OUTPUT);
    digitalWrite(PIN_BLK, HIGH);                  // 背光

    pinMode(PIN_PPG, INPUT);
    analogReadResolution(12);                     // 0..4095
    // 若编译报 ADC_11db 未定义（个别核心版本），改成 ADC_ATTEN_DB_12
    analogSetPinAttenuation(PIN_PPG, ADC_11db);   // 量程到 ~3.3V

    tft.begin(PIN_CS, PIN_DC, PIN_RST, PIN_MOSI, PIN_SCK);
    tft.fillScreen(C_BLACK);
    tft.setTextBackground(C_BLACK);

    // 开机 1.5s 显示版本号，便于确认烧的是哪一版
    tft.setCursor(30, 34);
    tft.setTextSize(1);
    tft.setTextColor(C_CYAN);
    tft.print(FW_VERSION);
    delay(1500);
    tft.fillScreen(C_BLACK);

    // 固定标题（位于信息区上方，不会被局部清屏擦掉）
    tft.setCursor(2, 3);
    tft.setTextSize(1);
    tft.setTextColor(C_CYAN);
    tft.print("XD-58C PPG  GPIO2");

    Serial.print("XD-58C heart rate "); Serial.println(FW_VERSION);
    Serial.println("S -> GPIO2 (ADC1_CH1), VCC -> 3.3V; MAX30102 GPIO14/13 untouched");
    Serial.println("每 2s 打印一行 [SIG] 诊断；BPM/IBI 输出格式与参考算法一致");

    nextSampleUs = micros() + SAMPLE_INTERVAL_US;
    lastUiMs  = millis();
    lastDbgMs = millis();
}

// ============================================================
void loop() {
    uint32_t nowUs = micros();

    // ---- 严格 2ms 节拍采样（追帧 + 掉帧统计）----
    if ((int32_t)(nowUs - nextSampleUs) >= 0) {
        uint32_t late = nowUs - nextSampleUs;
        nextSampleUs += SAMPLE_INTERVAL_US;
        if (late > (uint32_t)STALL_LIMIT_US) {
            nextSampleUs = nowUs + SAMPLE_INTERVAL_US;   // 落后过多：重新对齐，不连续补采样
            stallCount++;
        }
        sampleOnce();
        drawWaveStep();
    }

    // ---- UI：仅在有变化时绘制，且单次绘制量可控 ----
    if (millis() - lastUiMs >= 300) {
        lastUiMs = millis();
        drawUi();
    }

    // ---- 诊断：每 2s 一行，用于核对信号质量与是否掉帧 ----
    if (millis() - lastDbgMs >= 2000) {
        lastDbgMs = millis();
        Serial.printf("[SIG] raw=%d ac=%d amp=%d finger=%d ibiN=%u bpm=%d stalls=%lu\n",
                      lastRaw, lastAc, sigMax - sigMin,
                      fingerOn ? 1 : 0, ibiCnt, (int)(bpm + 0.5f), (unsigned long)stallCount);
    }
}
