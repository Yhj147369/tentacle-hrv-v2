# 心率联动 AI 遥控系统

平板摄像头 + 心率手环 + DeepSeek 多模态 + ESP32 蓝牙玩具 的实时闭环。

## 零安装设计
- **平板**: 不装任何东西, 浏览器打开链接即可(页面由电脑 Flask 渲染)。
- **手表/手环**: 只开蓝牙广播, ESP32 扫描连接读标准心率服务 0x180D。
- **一切处理在电脑**: 图片/AI/熔断/指令全在 server.py。

## 文件说明
| 文件 | 作用 |
| --- | --- |
| server.py | 主服务(Flask+串口+心率熔断+DeepSeek) |
| esp32_firmware.ino | ESP32 固件(连手环+玩具, 看门狗) |
| index.html | 平板摄像头页(Flask 直接渲染) |
| ble_scan.py / ble_services.py / ble_write.py | BLE 逆向工具 |
| start_server.bat | 一键启动服务(含 API Key) |
| flash.bat | 一键烧录(需 arduino-cli) |
| requirements.txt | Python 依赖 |

## 一、电脑端(Windows)
```bat
pip install -r requirements.txt
```
(国内慢加镜像: `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt`)

启动(HTTPS 供平板摄像头; 首次需先生成证书):
```bat
mkdir certs
openssl req -x509 -newkey rsa:2048 -keyout certs\key.pem -out certs\cert.pem -days 365 -nodes -subj "/CN=server"
start_server.bat        # 或 python server.py --port 5000 --https
```
平板访问: `https://电脑IP:5000/`(证书警告点"继续前往")。

## 二、ESP32 固件(VS Code + Arduino 插件)
1. VS Code 装 "Arduino Unified" 插件(已装)。
2. 打开 esp32_firmware.ino, 检查配置区:
   - HR_NAME_FILTER(默认 "Heart", 按手环改)
   - TOY_NAME_FILTER(默认 "GK36", 玩具协议见 逆向指南.md)
3. 连上 ESP32(USB), 按下方烧录。

## 三、VS Code 一键烧录
**方法 A - 插件按钮(推荐)**:
1. 打开 esp32_firmware.ino。
2. 右下角状态栏: 选开发板 **ESP32 Dev Module**(或 NodeMCU-32S), 端口选 **自动列出** 的 COM 口。
3. 点右上角 **→(上传)** 按钮, 或按 `Ctrl+Alt+U`。自动编译+烧录。

**方法 B - 命令面板**:
`Ctrl+Shift+P` → "Arduino: Upload" → 按提示选板子/端口。

**方法 C - 终端命令行**(arduino-cli):
```bash
arduino-cli board list                              # 看端口(自动检测)
arduino-cli compile --fqbn esp32:esp32:esp32 esp32_firmware.ino
arduino-cli upload -p COM3 --fqbn esp32:esp32:esp32 esp32_firmware.ino
```
端口不确定时: 设备管理器→端口(COM和LPT) 看新出现的 COM 号。

烧录后开串口监视器(115200)应看到:
```
BRIDGE_ALT v2.0  (MAX_LEVEL=60)  board=ESP32
FOUND Heart -> ...
HR_CONNECTED
HR:75,IBI:800
```

## 四、串口协议
| 方向 | 指令 | 说明 |
| --- | --- | --- |
| PC→ESP32 | PING | 返回 PONG |
| PC→ESP32 | STATUS | 返回状态 |
| PC→ESP32 | BEAT | 看门狗心跳(每2秒) |
| PC→ESP32 | STOP | 立即归零 |
| PC→ESP32 | SET <0-100> <秒> <波形> | 强度/时长/波形(constant/sine/pulse/random) |
| PC→ESP32 | RAW <hex> | 透传字节(逆向调试) |
| ESP32→PC | HR:75,IBI:800 | 心率上报(每秒) |

## 五、安全(三层)
1. PC 熔断: 心率>130强制STOP; 110~130限幅50。
2. ESP32 硬上限 MAX_LEVEL=60(PC无法绕过)。
3. ESP32 看门狗: 3秒无指令自动归零。

## 六、逆向
玩具 BLE 协议逆向: 见 **逆向指南.md**(按步骤直接执行)。

## 七、常见问题
- 平板打不开摄像头: 必须 https 访问。
- AI 输出空: 确认模型是 deepseek-v4-flash-vision-exp 且 max_tokens 够大。
- 心率显示"-": 手环广播名是否含 HR_NAME_FILTER 关键字。
- 玩具不动: 先按逆向指南第3步用 ble_write.py 试写确认协议。
