import os
import queue
import sys
import json
import threading
import time
import subprocess

#REPLACE THIS WITH YOUR USERNAME (example: pi)
USER_NAME = "pi"  

os.environ['XDG_DATA_HOME'] = f"/home/{USER_NAME}/.local/share"
os.environ['ARGOS_DEVICE_TYPE'] = 'cpu'

import customtkinter as ctk
import sounddevice as sd
from vosk import Model, KaldiRecognizer
import argostranslate.translate
import argostranslate.package
BASE_PATH = f"/home/{USER_NAME}/HearMe"
MODEL_PATHS = {
    "ru": os.path.join(BASE_PATH, "model_ru"),
    "en": os.path.join(BASE_PATH, "model_en")
}

SAMPLE_RATE = 48000
CLEANUP_DELAY = 15000  
PINS = {"in": 23, "out": 24, "theme": 25}
class Pi5Button:
    """Класс для работы с GPIO на Raspberry Pi 5 через системную утилиту pinctrl"""
    def __init__(self, pin):
        self.pin = pin
        try:
            subprocess.run(["pinctrl", "set", str(self.pin), "ip", "pu"], check=True)
        except Exception as e:
            print(f"Ошибка настройки пина {self.pin}: {e}")

    @property
    def is_pressed(self):
        try:
            res = subprocess.check_output(["pinctrl", "get", str(self.pin)], text=True)
            return "lo" in res  
        except: 
            return False

class HearMeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("HearMe Pro")
        self.attributes('-fullscreen', True)
        ctk.set_appearance_mode("Dark")
        self.input_lang = "ru"
        self.output_lang = "en"
        self.is_dark = True
        self.is_running = False
        self.audio_queue = queue.Queue()
        self.cleanup_timer = None
        self.partial_active = False
        self.last_press = {"in": 0, "out": 0, "theme": 0}

        print(f"--- ЗАГРУЗКА МОДЕЛЕЙ ИЗ {BASE_PATH} ---")
        try:
            m_ru = Model(MODEL_PATHS["ru"])
            m_en = Model(MODEL_PATHS["en"])
            self.recognizers = {
                "ru": KaldiRecognizer(m_ru, SAMPLE_RATE),
                "en": KaldiRecognizer(m_en, SAMPLE_RATE)
            }
            print("--- ВСЕ МОДЕЛИ ЗАГРУЖЕНЫ ---")
        except Exception as e:
            print(f"Критическая ошибка загрузки моделей: {e}")
            sys.exit(1)

        self.setup_ui()
        self.setup_hardware()

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        
        ctk.CTkLabel(self.sidebar, text="HearMe", font=("Arial", 28, "bold")).grid(row=0, column=0, pady=25)
        self.btn_start = ctk.CTkButton(self.sidebar, text="СТАРТ", fg_color="#27ae60", height=60, font=("Arial", 20, "bold"), command=self.toggle_service)
        self.btn_start.grid(row=1, column=0, padx=20, pady=10, sticky="ew")

        ctk.CTkLabel(self.sidebar, text="Язык речи:").grid(row=2, column=0, pady=(20,0))
        self.seg_in = ctk.CTkSegmentedButton(self.sidebar, values=["RU", "EN"], command=self.set_in_lang)
        self.seg_in.set("RU")
        self.seg_in.grid(row=3, column=0, padx=20, pady=5, sticky="ew")

        ctk.CTkLabel(self.sidebar, text="Перевод на:").grid(row=4, column=0, pady=(20,0))
        self.seg_out = ctk.CTkSegmentedButton(self.sidebar, values=["RU", "EN"], command=self.set_out_lang)
        self.seg_out.set("EN")
        self.seg_out.grid(row=5, column=0, padx=20, pady=5, sticky="ew")

        self.sw_theme = ctk.CTkSwitch(self.sidebar, text="Тёмная тема", command=self.toggle_theme)
        self.sw_theme.select()
        self.sw_theme.grid(row=6, column=0, padx=20, pady=30)

        ctk.CTkButton(self.sidebar, text="ВЫХОД", fg_color="transparent", border_width=1, command=self.close_app).grid(row=9, column=0, padx=20, pady=20, sticky="ew")

        self.textbox = ctk.CTkTextbox(self, font=("Arial", 40), wrap="word", fg_color="transparent")
        self.textbox.grid(row=0, column=1, padx=30, pady=30, sticky="nsew")
        self.log_msg("Система готова")

    def setup_hardware(self):
        self.hw_btns = {k: Pi5Button(v) for k, v in PINS.items()}
        self.check_hw_loop()

    def check_hw_loop(self):
        now = time.time()
        for key, btn in self.hw_btns.items():
            if btn.is_pressed and (now - self.last_press[key] > 0.6):
                self.handle_hw_click(key)
                self.last_press[key] = now
        self.after(100, self.check_hw_loop)

    def handle_hw_click(self, key):
        if key == "in":
            val = "EN" if self.input_lang == "ru" else "RU"
            self.seg_in.set(val)
            self.set_in_lang(val)
        elif key == "out":
            val = "EN" if self.output_lang == "ru" else "RU"
            self.seg_out.set(val)
            self.set_out_lang(val)
        elif key == "theme": 
            self.toggle_theme()

    def set_in_lang(self, val): self.input_lang = val.lower()
    def set_out_lang(self, val): self.output_lang = val.lower()

    def toggle_theme(self):
        self.is_dark = not self.is_dark
        ctk.set_appearance_mode("Dark" if self.is_dark else "Light")
        self.sw_theme.select() if self.is_dark else self.sw_theme.deselect()

    def toggle_service(self):
        if self.is_running:
            self.is_running = False
            self.btn_start.configure(text="СТАРТ", fg_color="#27ae60")
        else:
            self.is_running = True
            self.btn_start.configure(text="СТОП", fg_color="#c0392b")
            self.clear_ui()
            threading.Thread(target=self.run_rec, daemon=True).start()

    def run_rec(self):
        try:
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=4000, dtype='int16', channels=1, callback=lambda d,f,t,s: self.audio_queue.put(bytes(d))):
                while self.is_running:
                    data = self.audio_queue.get()
                    active_rec = self.recognizers[self.input_lang]
                    
                    if active_rec.AcceptWaveform(data):
                        txt = json.loads(active_rec.Result()).get("text", "")
                        if txt: self.after(0, lambda: self.show_txt(txt, False))
                    else:
                        ptl = json.loads(active_rec.PartialResult()).get("partial", "")
                        if ptl: self.after(0, lambda: self.show_txt(ptl, True))
        except Exception as e: 
            print(f"Ошибка аудиопотока: {e}")
            self.is_running = False

    def show_txt(self, text, is_partial):
        self.reset_timer()
        self.textbox.configure(state="normal")
        if self.partial_active: 
            self.textbox.delete("end-2l", "end-1c")
        
        if is_partial:
            self.textbox.insert("end", f"\n... {text}")
            self.partial_active = True
        else:
            res = f"\n{text.capitalize()}\n"
            if self.input_lang != self.output_lang:
                try:
                    tr = argostranslate.translate.translate(text, self.input_lang, self.output_lang)
                    res += f"{tr.capitalize()}\n"
                except Exception as e: 
                    print(f"Ошибка перевода: {e}")
            self.textbox.insert("end", res)
            self.partial_active = False
            
        self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def reset_timer(self):
        if self.cleanup_timer: 
            self.after_cancel(self.cleanup_timer)
        self.cleanup_timer = self.after(CLEANUP_DELAY, self.clear_ui)

    def clear_ui(self):
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")
        self.partial_active = False

    def log_msg(self, m):
        self.textbox.configure(state="normal")
        self.textbox.insert("end", f"[{m}]\n")
        self.textbox.configure(state="disabled")

    def close_app(self):
        self.is_running = False
        self.destroy()
        sys.exit(0)

if __name__ == "__main__":
    app = HearMeApp()
    app.mainloop()