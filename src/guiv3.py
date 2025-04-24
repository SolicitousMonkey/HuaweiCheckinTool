# Copyright © 2025 weiming z
# Released under the MIT License. See LICENSE for details.

import json, os, sys, time, requests, random
from pathlib import Path
from datetime import date, datetime, timedelta

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui  import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QTextEdit,
    QGridLayout, QCheckBox, QPushButton, QFormLayout, QVBoxLayout,
    QMessageBox, QSpinBox, QHBoxLayout, QSizePolicy
)

CONFIG_FILE = "config.txt"
QUERY_URL   = "https://hr-welcometo.huawei.com/obdService/api/app/heo/checkinplace_query"
BOOK_URL    = "https://hr-welcometo.huawei.com/obdService/api/app/heo/checkininfo_save"
ICON_PATH   = "icon.png"

def load_headers() -> dict:
    cfg = Path(CONFIG_FILE)
    if not cfg.exists():
        raise FileNotFoundError(f"配置文件 {CONFIG_FILE} 不存在！")
    headers = {}
    for line in cfg.read_text(encoding="utf-8").splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k] = v

    if "X-Jalor-Tenantalias" not in headers:
        print(f"{ts()} [WARN]config.txt 缺少租户信息字段 X-Jalor-Tenantalias，可能导致接口失败")

    return headers

def monday_range(start: date, end: date):
    if start.weekday() != 0:
        start += timedelta(days=(7 - start.weekday()))
    while start <= end:
        yield start.strftime("%Y-%m-%d")
        start += timedelta(days=7)

def ts() -> str:
    return datetime.now().strftime("[%H:%M:%S] ")

# ───────── Worker 线程 ─────────
class Worker(QThread):
    log_sig    = Signal(str)
    finish_sig = Signal(str)

    def __init__(self, auth_code: str, dates: list[str], interval: int):
        super().__init__()
        self.auth_code    = auth_code.strip()
        self.target_dates = dates
        self.interval     = interval
        self._running     = True

    def stop(self):
        self._running = False

    def _sleep_interruptible(self, seconds: int, step: float = 0.5):
        remaining = seconds
        while self._running and remaining > 0:
            time.sleep(min(step, remaining))
            remaining -= step

    def run(self):
        try:
            headers = load_headers()
            if self.auth_code:
                headers["Authorization"] = self.auth_code
        except Exception as e:
            self.log_sig.emit(f"[ERROR] {e}\n")
            return

        while self._running:
            self.log_sig.emit(f"{ts()} 查询可约日期…")
            datelist = self._query_available_slots(headers)
            if datelist is None:
                self._running = False
                break

            line = " | ".join(
                f"{'✓' if it['date'] in self.target_dates else '─'} {it['date']}" for it in datelist
            )
            self.log_sig.emit(line)

            matched = [it for it in datelist if it["date"] in self.target_dates]
            if matched:
                for item in matched:
                    if self._try_booking(headers, item):
                        self.finish_sig.emit(item["date"])
                        self._running = False
                        return
            else:
                dates_str = ", ".join(self.target_dates)
                self.log_sig.emit(
                    f"{ts()} 当前无 {dates_str} 的预约名额，{self.interval}s 后重试\n"
                )

            self._sleep_interruptible(self.interval)

    # ---------- helpers ----------
    # def _query_available_slots(self, headers):
    #     try:
    #         r = requests.post(QUERY_URL, headers=headers, json={})
    #         r.raise_for_status()
    #         data = r.json()
    #         if "data" not in data or "dateList" not in data["data"]:
    #             self.log_sig.emit(f"{ts()} Cookie 已过期或登录失效，请更新config.txt文件\n")
    #             return None
    #         return data["data"]["dateList"]
    #     except Exception as e:
    #         self.log_sig.emit(f"{ts()} [DEBUG] {e}\n")
    #         return None

    def _query_available_slots(self, headers):
        try:
            r = requests.post(QUERY_URL, headers=headers, json={}, timeout=(3, 10))
            r.raise_for_status()

            if not r.text.strip():
                self.log_sig.emit(f"{ts()} [DEBUG] 响应为空！状态码: {r.status_code}\n")
                return None

            try:
                data = r.json()
            except Exception as e:
                self.log_sig.emit(f"{ts()} [DEBUG] JSON解析失败，返回内容: {r.text[:200]!r}\n")
                return None

            if "data" not in data or "dateList" not in data["data"]:
                self.log_sig.emit(f"{ts()} [DEBUG] 返回数据结构错误，data: {data}\n")
                return None

            return data["data"]["dateList"]

        except Exception as e:
            self.log_sig.emit(f"{ts()} [DEBUG] 请求异常: {e}\n")
            return None
    def _try_booking(self, headers, item):
        payload = {
            "localeId": "zh_CN",
            "data": {
                "effectdate": item["date"],
                "newonbrdtcity": item.get("newonbrdtcity"),
                "onbrdaddress": item.get("onbrdaddress"),
            }
        }
        self.log_sig.emit(ts() + f"提交预约 {item['date']} …")
        r = requests.post(BOOK_URL, headers=headers, json=payload, timeout=(3, 10))
        r.raise_for_status()
        resp = r.json()

        if str(resp.get("success")).lower() == "true" or resp.get("code") in (0, "0"):
            self.log_sig.emit(ts() + "🎉🎉🎉预约成功🎉🎉🎉")
            return True
        else:
            self.log_sig.emit(ts() + f"预约失败: {resp.get('message', resp)}\n")
            return False


# ───────── 主窗口 ─────────
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("华为入职预约工具")
        self.setMinimumWidth(650)
        if Path(ICON_PATH).exists():
            self.setWindowIcon(QIcon(ICON_PATH))

        title = QLabel("华为入职预约工具")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("margin-bottom:16px;")

        self.auth_edit = QLineEdit()
        self.auth_edit.setPlaceholderText("请输入授权码")

        # 查询间隔
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(10)
        self.interval_spin.setSuffix(" 秒")

        # -------- 入职城市选择 --------
        city_row = QHBoxLayout()
        self.city_dg = QCheckBox("东莞")
        self.city_sh = QCheckBox("上海（已约满）")
        self.city_dg.setChecked(True)
        self.city_sh.setEnabled(False)  # 灰掉不可选
        city_row.addWidget(self.city_dg)
        city_row.addWidget(self.city_sh)
        city_row.addStretch()

        # -------- 日期复选框 --------
        self.date_checks = []
        grid = QGridLayout()
        start_d, end_d = date(2025, 5, 12), date(2025, 12, 22)
        col = row = 0
        for d in monday_range(start_d, end_d):
            cb = QCheckBox(datetime.strptime(d, "%Y-%m-%d").strftime("%m月%d日"))
            cb.setProperty("date", d)
            self.date_checks.append(cb)
            grid.addWidget(cb, row, col)
            col += 1
            if col == 4:
                col, row = 0, row + 1

        # -------- 按钮 --------
        self.submit_btn = QPushButton("开始抢约")
        self.submit_btn.clicked.connect(self.start_worker)
        self.query_btn = QPushButton("查询可约时间")
        self.query_btn.clicked.connect(self.single_query)
        self.stop_btn = QPushButton("停止抢约")
        self.stop_btn.clicked.connect(self.stop_worker)
        self.stop_btn.setEnabled(False)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        form = QFormLayout()
        # form.addRow("授权码（可选）：", self.auth_edit)
        form.addRow("查询间隔（秒）：", self.interval_spin)
        form.addRow(QLabel("入职城市："))
        form.addRow(city_row)
        form.addRow(QLabel("预约日期（可多选）："))
        form.addRow(grid)

        button_layout = QHBoxLayout()
        for btn in (self.submit_btn, self.query_btn, self.stop_btn):
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            btn.setMinimumWidth(120)
            button_layout.addWidget(btn)

        form.addRow(button_layout)
        form.addRow(self.log_box)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(form)

        self.worker: Worker | None = None

    # -------- 槽函数 --------
    def start_worker(self):
        dates = [cb.property("date") for cb in self.date_checks if cb.isChecked()]
        if not dates:
            self.log_box.append("[WARN] 请至少选择一个日期！\n")
            return

        interval = self.interval_spin.value()
        self.submit_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log_box.append(f"{ts()} 开始后台抢号任务，间隔 {interval}s …")

        self.worker = Worker(self.auth_edit.text(), dates, interval)
        self.worker.log_sig.connect(self.log_box.append)
        self.worker.finish_sig.connect(self.on_finish)
        self.worker.finished.connect(self.on_thread_finished)
        self.worker.start()

    def on_finish(self, date_str: str):
        self.log_box.append(f"\n🎉 已成功预约 {date_str}，程序结束\n")
        self.on_thread_finished()

    def on_thread_finished(self):
        self.submit_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        self.log_box.append(f"{ts()} 后台任务已结束\n")

    def stop_worker(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1000)
        else:
            self.log_box.append("[WARN] 当前没有正在运行的抢约任务\n")

    def single_query(self):
        try:
            headers = load_headers()
            if code := self.auth_edit.text().strip():
                headers["Authorization"] = code
            self.log_box.append(f"{ts()} 正在查询可约时间…\n")
            r = requests.post(QUERY_URL, headers=headers, json={})
            r.raise_for_status()
            date_list = r.json().get("data", {}).get("dateList", [])
            if not date_list:
                self.log_box.append("[INFO] 当前没有放号\n")
                return

            cities = {}
            for it in date_list:
                cid = it.get("newonbrdtcity") or it.get("onbrdtcity")
                name = it.get("onbrdtcityName", "未知")
                if cid: cities[cid] = name
            city_info = "，".join(f"{v}({k})" for k, v in cities.items())
            self.log_box.append(ts() + f"当前开放城市: {city_info}\n")

            avail = ", ".join([d.get("date", "?") for d in date_list])
            self.log_box.append(f"{ts()} 当前可预约日期: {avail}\n")
        except FileNotFoundError as fe:
            QMessageBox.critical(self, "错误", str(fe))
        except Exception as e:
            self.log_box.append(f"{ts()} 查询失败: {e}\n")

    # ───────── 入口 ─────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    if Path(ICON_PATH).exists():
        app.setWindowIcon(QIcon(ICON_PATH))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
