import serial
import time

ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1)
time.sleep(1)

# 清空缓冲区
ser.reset_input_buffer()

print("=== 读取串口日志 (5秒) ===")
print("-" * 50)

start = time.time()
lines = []
while time.time() - start < 5:
    if ser.in_waiting > 0:
        line = ser.readline().decode('utf-8', errors='ignore')
        if line.strip():
            lines.append(line.strip())
            print(line.strip())

ser.close()
print("-" * 50)
print(f"共读取 {len(lines)} 行")