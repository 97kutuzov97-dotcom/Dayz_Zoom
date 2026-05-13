import ctypes
from ctypes import wintypes
import threading
import time
import json
import os
from pynput import keyboard, mouse
from PIL import Image, ImageFilter
import tkinter as tk
from tkinter import ttk
import pystray
from pystray import MenuItem as Item

# ==================== ГЛОБАЛЬНЫЕ НАСТРОЙКИ ====================
CONFIG_FILE = "dayz_zoom_config.json"

DEFAULT_CONFIG = {
    "toggle_key": "f2",
    "modifier_key": "ctrl",
    "reset_key": "mouse_middle",
    "default_zoom": 3.0,
    "zoom_step": 0.5,
    "zoom_min": 2.0,
    "zoom_max": 10.0,
    "reticle_visible": True,
    "reticle_alpha": 30,
    "reticle_color": "#808080",
}

config = DEFAULT_CONFIG.copy()
zoom_factor = config["default_zoom"]
zoom_active = False
need_zoom = False

# Размеры окна лупы
MAG_WIDTH = 400
MAG_HEIGHT = 400

# Дескрипторы Magnifier API
h_magnifier = None

# ==================== ЗАГРУЗКА КОНФИГА ====================
def load_config():
    global config, zoom_factor
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    zoom_factor = config["default_zoom"]

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# ==================== MAGNIFIER API ====================
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
mag_dll = ctypes.windll.magnification

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long)]

class MAGTRANSFORM(ctypes.Structure):
    _fields_ = [("v", ctypes.c_float * 3 * 3)]

# Настройка сигнатур функций Magnification API
mag_dll.MagInitialize.restype = wintypes.BOOL

mag_dll.MagUninitialize.restype = wintypes.BOOL

mag_dll.MagSetWindowSource.restype = wintypes.BOOL
mag_dll.MagSetWindowSource.argtypes = [wintypes.HWND, RECT]

mag_dll.MagSetWindowTransform.restype = wintypes.BOOL
mag_dll.MagSetWindowTransform.argtypes = [wintypes.HWND, ctypes.POINTER(MAGTRANSFORM)]

# Настройка User32 функций
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
]

user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT
]

user32.ShowWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

user32.DestroyWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]

user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

mag_dll.MagSetInputTransform.restype = wintypes.BOOL
mag_dll.MagSetInputTransform.argtypes = [wintypes.BOOL, ctypes.POINTER(RECT), ctypes.POINTER(RECT)]

class MAGCOLOREFFECT(ctypes.Structure):
    _fields_ = [("transform", ctypes.c_float * 5 * 5)]

mag_dll.MagSetColorEffect.restype = wintypes.BOOL
mag_dll.MagSetColorEffect.argtypes = [wintypes.HWND, ctypes.POINTER(MAGCOLOREFFECT)]

# Инициализация лупы
mag_dll.MagInitialize()

def create_hidden_magnifier():
    global h_magnifier
    if h_magnifier:
        return
    # Двойной размер для каскадного улучшения (опционально)
    w, h = MAG_WIDTH * 2, MAG_HEIGHT * 2
    h_magnifier = user32.CreateWindowExW(
        0x8 | 0x20 | 0x80000,  # WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_LAYERED
        "Magnifier", "MagnifierWindow",
        0x80000000,  # WS_POPUP
        -w, -h, w, h,
        None, None, None, None
    )
    # Отключить цветовые эффекты
    mag_dll.MagSetColorEffect(h_magnifier, None)
    user32.ShowWindow(h_magnifier, 8)  # SW_SHOWNOACTIVATE
    update_magnifier()

def destroy_magnifier():
    global h_magnifier
    if h_magnifier:
        user32.DestroyWindow(h_magnifier)
        h_magnifier = None

def update_magnifier():
    global h_magnifier, zoom_factor
    if not h_magnifier:
        return

    # Расчет исходного прямоугольника
    src_w = int(MAG_WIDTH / zoom_factor)
    src_h = int(MAG_HEIGHT / zoom_factor)
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    src_x = (screen_w - src_w) // 2
    src_y = (screen_h - src_h) // 2

    rect = RECT(src_x, src_y, src_x + src_w, src_y + src_h)
    mag_dll.MagSetWindowSource(h_magnifier, rect)

    # Настройка матрицы трансформации (используем единичную матрицу,
    # так как увеличение достигается за счет MagSetWindowSource)
    matrix = MAGTRANSFORM()
    matrix.v[0][0] = 1.0
    matrix.v[1][1] = 1.0
    matrix.v[2][2] = 1.0
    mag_dll.MagSetWindowTransform(h_magnifier, ctypes.byref(matrix))

# ==================== ОКНО ОВЕРЛЕЯ (ПУСТОЕ, НЕ ИСПОЛЬЗУЕМ, Т.К. ЛУПА САМА РИСУЕТ) ====================
# В этой реализации лупа сама отображает увеличенное изображение.
# Поэтому отдельное видимое окно не нужно, но мы управляем видимостью h_magnifier.
# Для отображения в нужном месте центрируем окно лупы размером 400x400.

def show_magnifier():
    if not h_magnifier:
        create_hidden_magnifier()
    user32.SetWindowPos(h_magnifier, None,
        (user32.GetSystemMetrics(0) - MAG_WIDTH) // 2,
        (user32.GetSystemMetrics(1) - MAG_HEIGHT) // 2,
        MAG_WIDTH, MAG_HEIGHT, 0x0040)  # SWP_SHOWWINDOW
    show_reticle()

def hide_magnifier():
    if h_magnifier:
        user32.ShowWindow(h_magnifier, 0)  # SW_HIDE
    hide_reticle()

# ==================== СЕТКА (RETICLE) ====================
reticle_window = None

def create_reticle():
    global reticle_window
    if reticle_window:
        return
    reticle_window = tk.Toplevel(root)
    reticle_window.overrideredirect(True)
    reticle_window.attributes("-topmost", True)
    reticle_window.attributes("-transparentcolor", "black")
    reticle_window.attributes("-alpha", config["reticle_alpha"] / 255.0)

    # Сделать окно сквозным для кликов (WS_EX_TRANSPARENT | WS_EX_LAYERED)
    hwnd = reticle_window.winfo_id()
    style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, style | 0x20 | 0x80000)

    canvas = tk.Canvas(reticle_window, width=MAG_WIDTH, height=MAG_HEIGHT,
                       bg="black", highlightthickness=0)
    canvas.pack()
    reticle_window.canvas = canvas
    draw_reticle()

def draw_reticle():
    if not reticle_window:
        return
    canvas = reticle_window.canvas
    canvas.delete("all")
    if not config["reticle_visible"]:
        return

    color = config["reticle_color"]
    mid_x, mid_y = MAG_WIDTH // 2, MAG_HEIGHT // 2

    # Рисуем простой перекрестие
    canvas.create_line(mid_x - 20, mid_y, mid_x + 20, mid_y, fill=color, width=1)
    canvas.create_line(mid_x, mid_y - 20, mid_x, mid_y + 20, fill=color, width=1)

def show_reticle():
    run_in_tk(_show_reticle_impl)

def _show_reticle_impl():
    if not config["reticle_visible"]:
        return
    if not reticle_window:
        create_reticle()

    x = (user32.GetSystemMetrics(0) - MAG_WIDTH) // 2
    y = (user32.GetSystemMetrics(1) - MAG_HEIGHT) // 2
    reticle_window.geometry(f"{MAG_WIDTH}x{MAG_HEIGHT}+{x}+{y}")
    reticle_window.attributes("-alpha", config["reticle_alpha"] / 255.0)
    reticle_window.deiconify()
    reticle_window.lift()

def hide_reticle():
    run_in_tk(_hide_reticle_impl)

def _hide_reticle_impl():
    if reticle_window:
        reticle_window.withdraw()

# ==================== УПРАВЛЕНИЕ ЗУМОМ ====================
def set_zoom(new_factor):
    global zoom_factor
    zoom_factor = round(new_factor, 1)
    if h_magnifier:
        update_magnifier()
    show_tooltip(f"Zoom: {zoom_factor}x")

def zoom_in():
    global zoom_factor
    if zoom_factor < config["zoom_max"]:
        set_zoom(zoom_factor + config["zoom_step"])

def zoom_out():
    global zoom_factor
    if zoom_factor > config["zoom_min"]:
        set_zoom(zoom_factor - config["zoom_step"])

def reset_zoom():
    set_zoom(config["default_zoom"])

# ==================== ПЕРЕХВАТ ГОРЯЧИХ КЛАВИШ ====================
modifier_pressed = False

def get_key_name(key):
    if hasattr(key, 'name'):
        name = key.name
        # Нормализация модификаторов
        if name.startswith('ctrl'): return 'ctrl'
        if name.startswith('shift'): return 'shift'
        if name.startswith('alt'): return 'alt'
        return name
    if hasattr(key, 'char') and key.char:
        return key.char.lower()
    return str(key).replace('Key.', '')

def on_press(key):
    global modifier_pressed, zoom_active, need_zoom
    key_name = get_key_name(key)

    # Проверяем модификатор
    if key_name == config["modifier_key"]:
        modifier_pressed = True
        update_need_zoom()

    # Переключение постоянного зума
    if key_name == config["toggle_key"].lower():
        toggle_persistent_zoom()

def on_release(key):
    global modifier_pressed, zoom_active, need_zoom
    key_name = get_key_name(key)

    if key_name == config["modifier_key"]:
        modifier_pressed = False
        update_need_zoom()

def update_need_zoom():
    global need_zoom
    new_state = zoom_active or modifier_pressed
    if new_state != need_zoom:
        need_zoom = new_state
        if need_zoom:
            show_magnifier()
        else:
            hide_magnifier()

def toggle_persistent_zoom():
    global zoom_active
    zoom_active = not zoom_active
    if zoom_active and not h_magnifier:
        create_hidden_magnifier()
    update_need_zoom()
    show_tooltip("Zoom ON" if zoom_active else "Zoom OFF")

# Обработчик колеса мыши
def on_scroll(x, y, dx, dy):
    if need_zoom:
        if dy > 0:
            zoom_in()
        else:
            zoom_out()

# Обработчик кнопок мыши
def on_click(x, y, button, pressed):
    if not pressed:
        return

    btn_name = f"mouse_{button.name}"
    if btn_name == config["reset_key"]:
        if need_zoom:
            reset_zoom()

# Запуск слушателей
keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
mouse_listener = mouse.Listener(on_scroll=on_scroll, on_click=on_click)

# ==================== ВСПЛЫВАЮЩАЯ ПОДСКАЗКА ====================
tooltip_window = None

def show_tooltip(text):
    run_in_tk(_show_tooltip_impl, text)

def _show_tooltip_impl(text):
    global tooltip_window
    if tooltip_window:
        try:
            tooltip_window.destroy()
        except:
            pass
    tooltip_window = tk.Toplevel(root)
    tooltip_window.overrideredirect(True)
    tooltip_window.attributes("-topmost", True)
    tooltip_window.attributes("-alpha", 0.8)
    label = tk.Label(tooltip_window, text=text, bg="black", fg="white", font=("Arial", 12))
    label.pack()
    # Позиция по центру экрана сверху
    screen_w = tooltip_window.winfo_screenwidth()
    screen_h = tooltip_window.winfo_screenheight()
    x = (screen_w - label.winfo_reqwidth()) // 2
    y = screen_h // 4
    tooltip_window.geometry(f"+{x}+{y}")
    # Закрыть через 1.2 секунды
    root.after(1200, lambda: _hide_tooltip_impl(tooltip_window))

def _hide_tooltip_impl(tw):
    try:
        if tw:
            tw.destroy()
    except:
        pass

# ==================== ОКНО НАСТРОЕК ====================
settings_window = None
capture_var = None
capture_button = None

def start_capture(var_name, btn):
    global capture_var, capture_button
    capture_var = var_name
    capture_button = btn
    btn.config(text="... press key/mouse ...")

    k_listener = None
    m_listener = None

    def stop_both():
        if k_listener: k_listener.stop()
        if m_listener: m_listener.stop()

    def on_cap_key(key):
        global capture_var, capture_button
        if capture_var:
            name = get_key_name(key)
            config[capture_var] = name
            run_in_tk(btn.config, text=name)
            save_config()
            capture_var = None
            capture_button = None
            stop_both()
            return False

    def on_cap_click(x, y, button, pressed):
        global capture_var, capture_button
        if capture_var and pressed:
            name = f"mouse_{button.name}"
            config[capture_var] = name
            run_in_tk(btn.config, text=name)
            save_config()
            capture_var = None
            capture_button = None
            stop_both()
            return False

    k_listener = keyboard.Listener(on_press=on_cap_key)
    m_listener = mouse.Listener(on_click=on_cap_click)
    k_listener.start()
    m_listener.start()

def open_settings():
    global settings_window
    if settings_window and tk.Toplevel.winfo_exists(settings_window):
        settings_window.deiconify()
        return
    settings_window = tk.Toplevel()
    settings_window.title("DayZ Zoom Settings")
    settings_window.geometry("300x400")
    settings_window.attributes("-topmost", True)

    # Toggle key
    ttk.Label(settings_window, text="Toggle Zoom:").pack(pady=5)
    toggle_btn = ttk.Button(settings_window, text=config["toggle_key"])
    toggle_btn.pack()
    toggle_btn.config(command=lambda: start_capture("toggle_key", toggle_btn))

    # Modifier
    ttk.Label(settings_window, text="Modifier (hold for wheel):").pack(pady=5)
    mod_btn = ttk.Button(settings_window, text=config["modifier_key"])
    mod_btn.pack()
    mod_btn.config(command=lambda: start_capture("modifier_key", mod_btn))

    # Reset
    ttk.Label(settings_window, text="Reset zoom:").pack(pady=5)
    reset_btn = ttk.Button(settings_window, text=config["reset_key"])
    reset_btn.pack()
    reset_btn.config(command=lambda: start_capture("reset_key", reset_btn))

    # Default zoom
    ttk.Label(settings_window, text="Default zoom:").pack(pady=5)
    zoom_var = tk.DoubleVar(value=config["default_zoom"])
    zoom_scale = ttk.Scale(settings_window, from_=2.0, to=10.0, variable=zoom_var, orient="horizontal")
    zoom_scale.pack()
    zoom_var.trace("w", lambda *a: update_config("default_zoom", zoom_var.get()))

    # Reticle visible
    reticle_var = tk.BooleanVar(value=config["reticle_visible"])
    ttk.Checkbutton(settings_window, text="Reticle visible", variable=reticle_var,
                    command=lambda: update_config("reticle_visible", reticle_var.get())).pack(pady=5)

    # Reticle alpha
    ttk.Label(settings_window, text="Reticle alpha (0-255):").pack(pady=5)
    alpha_var = tk.IntVar(value=config["reticle_alpha"])
    alpha_scale = ttk.Scale(settings_window, from_=0, to=255, variable=alpha_var, orient="horizontal")
    alpha_scale.pack()
    alpha_var.trace("w", lambda *a: update_config("reticle_alpha", alpha_var.get()))

def update_config(key, value):
    config[key] = value
    if key == "default_zoom":
        global zoom_factor
        if need_zoom:
            set_zoom(value)
        else:
            zoom_factor = value
    elif key == "reticle_visible":
        if need_zoom:
            if value: show_reticle()
            else: hide_reticle()
    elif key == "reticle_alpha" or key == "reticle_color":
        run_in_tk(draw_reticle)
        if need_zoom:
            run_in_tk(lambda: reticle_window.attributes("-alpha", config["reticle_alpha"] / 255.0) if reticle_window else None)
    save_config()

# ==================== ТРЕЙ ====================
def create_tray():
    icon_image = Image.new('RGB', (64, 64), color='gray')
    menu = pystray.Menu(Item('Settings', open_settings), Item('Exit', exit_app))
    tray = pystray.Icon("DayZZoom", icon_image, "DayZ Zoom", menu)
    return tray

def exit_app(icon=None):
    hide_magnifier()
    destroy_magnifier()
    mag_dll.MagUninitialize()
    keyboard_listener.stop()
    mouse_listener.stop()
    if icon:
        icon.stop()
    os._exit(0)

# ==================== ПОТОКОБЕЗОПАСНЫЕ ОБНОВЛЕНИЯ UI ====================
def run_in_tk(func, *args, **kwargs):
    root.after(0, lambda: func(*args, **kwargs))

# ==================== ЗАПУСК ====================
root = tk.Tk()
root.withdraw()

if __name__ == "__main__":
    load_config()
    keyboard_listener.start()
    mouse_listener.start()

    tray = create_tray()
    threading.Thread(target=tray.run, daemon=True).start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        exit_app()
