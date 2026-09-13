#pragma once
// ============================================================
// AD8232 单导联心电(ECG)传感器驱动
// 纯手写实现，无第三方依赖（AD8232 输出模拟信号，直接接 ADC 读取）
// 接法: VCC->5V, GND->GND, OUTPUT->GPIO1(ADC1_CH1)
//       LO(+)/RA(-)/RL 接电极贴胸前
// 输出: ecg_hr(心电图心率 bpm)，内部峰值检测窗口 4s
// ============================================================
#include <Arduino.h>

class AD8232 {
public:
    bool begin(int pin);
    // 采集一次心率。返回 true = 有结果。
    // 内部以 ~250Hz 连续采样一个 4s 窗口；窗口未满则沿用上次结果
    bool read(float &ecg_hr_bpm);

private:
    void processWindow(float &ecg_hr_bpm);
    int  _pin = -1;
    static const int BUF = 1000;   // ~250Hz × 4s
    int  _buf[BUF];
    int  _write = 0;
    bool _full  = false;
};
