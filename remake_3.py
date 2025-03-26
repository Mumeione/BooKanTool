"""
图书PDF生成工具

该工具用于从Android模拟器中提取杂志图片,
并按指定顺序重命名后转换为PDF文件。
主要功能包括ADB连接、文件传输、图片重命名和PDF生成。

作者: Mumei
版本: 1.0
日期: 2025-03-26

"""

import configparser
import logging
import os
import shutil
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, ttk
from tkinter import messagebox
from pathlib import Path
from threading import Thread
from PIL import Image, ImageTk, UnidentifiedImageError
import img2pdf

# 全局颜色配置
COLOR_PRIMARY = "#3498db"
COLOR_SECONDARY = "#2980b9"
COLOR_LIGHT = "#ecf0f1"
COLOR_DARK = "#2c3e50"
COLOR_SUCCESS = "#2ecc71"
COLOR_WARNING = "#f39c12"
COLOR_DANGER = "#e74c3c"


class ModernButton(ttk.Button):
    """现代化按钮控件"""

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.style = ttk.Style()
        self.style.configure("Modern.TButton",
                             foreground=COLOR_DARK,
                             background=COLOR_PRIMARY,
                             font=("微软雅黑", 10),
                             padding=8,
                             relief="flat")
        self.configure(style="Modern.TButton")
        self.style.map("Modern.TButton",
                       background=[("active", COLOR_SECONDARY),
                                   ("disabled", "#95a5a6")],
                       relief=[("pressed", "sunken"), ("!pressed", "flat")])


class ModernEntry(ttk.Entry):
    """现代化输入框控件"""

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.style = ttk.Style()
        self.style.configure("Modern.TEntry",
                             fieldbackground=COLOR_LIGHT,
                             foreground=COLOR_DARK,
                             bordercolor=COLOR_PRIMARY,
                             lightcolor=COLOR_PRIMARY,
                             darkcolor=COLOR_PRIMARY,
                             padding=5)
        self.configure(style="Modern.TEntry")


class ModernCombobox(ttk.Combobox):
    """现代化下拉框控件"""

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.style = ttk.Style()
        self.style.configure("Modern.TCombobox",
                             fieldbackground=COLOR_LIGHT,
                             foreground=COLOR_DARK,
                             selectbackground=COLOR_PRIMARY,
                             selectforeground="white",
                             padding=5)
        self.configure(style="Modern.TCombobox")


class ModernLabelFrame(ttk.LabelFrame):
    """现代化标签框架控件"""

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.style = ttk.Style()
        self.style.configure("Modern.TLabelframe",
                             background=COLOR_LIGHT,
                             foreground=COLOR_DARK,
                             bordercolor=COLOR_PRIMARY)
        self.style.configure("Modern.TLabelframe.Label",
                             foreground=COLOR_PRIMARY)
        self.configure(style="Modern.TLabelframe")


def configure_styles():
    """配置全局样式"""
    style = ttk.Style()

    # 主窗口背景
    style.configure('TFrame', background=COLOR_LIGHT)

    # 按钮样式
    style.configure('Modern.TButton',
                    foreground=COLOR_DARK,
                    background=COLOR_PRIMARY,
                    font=('微软雅黑', 10),
                    padding=8,
                    relief='flat')
    style.map('Modern.TButton',
              background=[('active', COLOR_SECONDARY),
                          ('disabled', '#95a5a6')],
              relief=[('pressed', 'sunken'), ('!pressed', 'flat')])

    # 输入框样式
    style.configure('Modern.TEntry',
                    fieldbackground=COLOR_LIGHT,
                    foreground=COLOR_DARK,
                    bordercolor=COLOR_PRIMARY,
                    lightcolor=COLOR_PRIMARY,
                    darkcolor=COLOR_PRIMARY,
                    padding=5)

    # 下拉框样式
    style.configure('Modern.TCombobox',
                    fieldbackground=COLOR_LIGHT,
                    foreground=COLOR_DARK,
                    selectbackground=COLOR_PRIMARY,
                    selectforeground='white',
                    padding=5)

    # 标签框架样式
    style.configure('Modern.TLabelframe',
                    background=COLOR_LIGHT,
                    foreground=COLOR_DARK,
                    bordercolor=COLOR_PRIMARY)
    style.configure('Modern.TLabelframe.Label',
                    foreground=COLOR_PRIMARY)

    # 状态栏样式
    style.configure('Status.TLabel',
                    background=COLOR_DARK,
                    foreground='white',
                    padding=5,
                    font=('微软雅黑', 9))

    # 标签样式
    style.configure('Modern.TLabel',
                    background=COLOR_LIGHT,
                    foreground=COLOR_DARK,
                    font=('微软雅黑', 10))

    # 进度条样式
    style.configure('Modern.Horizontal.TProgressbar',
                    background=COLOR_PRIMARY,
                    troughcolor=COLOR_LIGHT,
                    bordercolor=COLOR_PRIMARY,
                    lightcolor=COLOR_PRIMARY,
                    darkcolor=COLOR_PRIMARY)


class WindowManager:
    """管理应用程序主窗口和UI组件的类"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title('图书PDF生成工具 v1.0')

        # 配置全局样式
        configure_styles()

        # 设置窗口图标
        try:
            self.root.iconbitmap('app_icon.ico')
        except tk.TclError:  # 捕获特定的 TclError 异常
            pass

        # 初始化所有GUI组件属性
        self.main_container = None
        self.button_frame = None
        self.adb_frame = None
        self.path_frame = None
        self.status_bar = None
        self.progress_bar = None

        # 配置相关属性
        self.config_file = 'preferences.cfg'
        self.source_dir = os.path.expanduser('~/Documents/magazine_images')
        self.target_dir = os.path.expanduser('~/Documents/Books/magazine_pdfs')

        # ADB配置属性
        self.adb_port = '7555'
        self.emulator_path = '/sdcard/Android/data/cn.com.bookan/files/bookan/magazine'

        # 加载配置
        self.load_preferences()

        # 设置窗口关闭事件处理
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # 创建主窗口
        self.create_main_window()

        # 添加动画效果
        self.root.attributes('-alpha', 0.0)
        self.root.after(100, lambda: self.root.attributes('-alpha', 0.9))
        self.root.after(200, lambda: self.root.attributes('-alpha', 1.0))

    def center_window_on_parent(self, window, width=None, height=None):
        """将窗口居中于父窗口"""
        if width and height:
            window.geometry(f"{width}x{height}")

        window.update_idletasks()

        # 获取父窗口位置和大小
        parent_x = self.root.winfo_x()
        parent_y = self.root.winfo_y()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()

        # 计算居中位置
        window_width = window.winfo_width()
        window_height = window.winfo_height()

        x = parent_x + (parent_width - window_width) // 2
        y = parent_y + (parent_height - window_height) // 2

        window.geometry(f"+{x}+{y}")

    def create_main_window(self):
        """创建主窗口布局"""
        # 主容器
        self.main_container = ttk.Frame(self.root, padding=(15, 10))
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # 标题框架 - 包含图标和标题
        title_frame = ttk.Frame(self.main_container)
        title_frame.pack(pady=(0, 15))

        # 尝试加载并显示图标
        try:
            # 加载图标图片
            icon_img = Image.open("app_icon.png")  # 假设图标文件名为app_icon.png
            icon_img = icon_img.resize(
                (50, 50), Image.Resampling.LANCZOS)  # 调整大小
            self.icon_photo = ImageTk.PhotoImage(icon_img)

            # 创建图标标签
            icon_label = ttk.Label(title_frame, image=self.icon_photo)
            icon_label.pack(side=tk.LEFT, padx=(0, 10))
        except (FileNotFoundError, UnidentifiedImageError) as e:
            print(f"无法加载图标: {e}")
            self.icon_photo = None
        # 标题标签
        title_label = ttk.Label(title_frame,
                                text="图书PDF生成工具",
                                font=("微软雅黑", 18, "bold"),
                                foreground=COLOR_PRIMARY)
        title_label.pack(side=tk.LEFT)

        # 按钮框架 - 移除了扫描设备按钮
        self.button_frame = ttk.Frame(self.main_container)
        self.button_frame.pack(fill=tk.X, pady=(0, 15))

        btn_config = ModernButton(
            self.button_frame, text='配置路径', command=self.open_config_dialog)
        btn_config.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)

        btn_quit = ModernButton(
            self.button_frame, text='退出程序', command=self.root.quit)
        btn_quit.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)

        # ADB配置框架
        self.adb_frame = ModernLabelFrame(self.main_container, text="ADB配置")
        self.adb_frame.pack(fill=tk.X, pady=(0, 15), padx=5)

        # 端口配置 - 将测试连接按钮放在同一行
        port_frame = ttk.Frame(self.adb_frame)
        port_frame.pack(fill=tk.X, pady=5)

        ttk.Label(port_frame, text='ADB端口:', style='Modern.TLabel').pack(
            side=tk.LEFT, padx=5)

        self.port_combo = ModernCombobox(
            port_frame,
            values=['MUMU-7555', '雷电-5555', '蓝叠-5555', '夜神-62001', '逍遥-21503'],
            state='normal'  # 改为可编辑状态
        )
        self.port_combo.set(self.adb_port)
        self.port_combo.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        # 测试连接按钮放在同一行
        btn_test = ModernButton(port_frame, text='测试连接',
                                command=self.test_adb_connection)
        btn_test.pack(side=tk.LEFT, padx=5)

        # 模拟器路径配置
        path_frame = ttk.Frame(self.adb_frame)
        path_frame.pack(fill=tk.X, pady=5)

        ttk.Label(path_frame, text='模拟器路径:', style='Modern.TLabel').pack(
            side=tk.LEFT, padx=5)

        self.entry_emu_path = ModernEntry(path_frame)
        self.entry_emu_path.insert(0, self.emulator_path)
        self.entry_emu_path.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        btn_browse = ModernButton(
            path_frame, text='浏览', command=self.browse_emulator_path)
        btn_browse.pack(side=tk.LEFT, padx=5)

        # 执行ADB复制按钮单独一行
        btn_frame = ttk.Frame(self.adb_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        btn_pull = ModernButton(btn_frame, text='执行ADB复制', command=lambda: Thread(
            target=self.adb_pull_and_process).start())
        btn_pull.pack(fill=tk.X, padx=5)

        # 输出路径配置
        output_frame = ModernLabelFrame(self.main_container, text="输出配置")
        output_frame.pack(fill=tk.X, pady=(0, 15), padx=5)

        target_frame = ttk.Frame(output_frame)
        target_frame.pack(fill=tk.X, pady=5)

        ttk.Label(target_frame, text='输出目录:', style='Modern.TLabel').pack(
            side=tk.LEFT, padx=5)

        self.entry_target = ModernEntry(target_frame)
        self.entry_target.insert(0, self.target_dir)
        self.entry_target.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        btn_target = ModernButton(
            target_frame, text='浏览', command=self.browse_target_dir)
        btn_target.pack(side=tk.LEFT, padx=5)

        # 处理按钮
        btn_process = ModernButton(
            self.main_container, text='开始处理', command=self.process_all)
        btn_process.pack(fill=tk.X, pady=(10, 0), padx=5)

        # 进度条
        self.progress_bar = ttk.Progressbar(self.main_container,
                                            style='Modern.Horizontal.TProgressbar',
                                            mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=(15, 2), padx=5)

        # 状态栏
        self.status_bar = ttk.Label(self.root,
                                    text='就绪',
                                    style='Status.TLabel',
                                    anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 事件绑定
        self.port_combo.bind('<<ComboboxSelected>>', self.on_port_selected)
        self.port_combo.bind('<FocusOut>', self.on_port_focus_out)

    def on_close(self):
        """窗口关闭事件处理"""
        # 保存窗口几何信息
        self.save_preferences()
        self.root.quit()

    def test_adb_connection(self):
        """测试ADB连接"""
        self.root.focus_set()  # 取消当前控件的焦点
        port = self.port_combo.get()

        # 检查端口是否为纯数字
        if not port.isdigit():
            messagebox.showerror("错误", "端口号必须为纯数字")
            return

        self.adb_port = port
        self.save_preferences()
        Thread(target=self.adb_connect).start()

    def on_port_selected(self, event=None):
        """端口选择事件处理"""
        selected = self.port_combo.get()
        port_value = selected.split('-')[-1] if '-' in selected else selected

        if port_value.isdigit():
            self.adb_port = port_value
            self.save_preferences()
            # 取消组合框的焦点
            self.root.focus_set()
            # 自动测试连接
            Thread(target=self.adb_connect).start()

    def on_port_focus_out(self, event=None):
        """端口输入框失去焦点事件处理"""
        port = self.port_combo.get()

        # 如果是预设值，不处理
        if any(port.startswith(prefix) for prefix in ['MUMU', '雷电', '蓝叠', '夜神', '逍遥']):
            return

        # 如果是自定义端口，检查是否为数字
        if port and not port.isdigit():
            messagebox.showerror("错误", "端口号必须为纯数字")
            self.port_combo.focus_set()
            return

        # 如果是有效端口，自动测试连接
        if port.isdigit():
            self.adb_port = port
            self.save_preferences()
            Thread(target=self.adb_connect).start()

    def adb_pull_and_process(self):
        """执行ADB复制并自动处理文件"""
        self.adb_pull()

        # ADB复制完成后自动处理文件
        self.process_all()

    def browse_emulator_path(self):
        """浏览模拟器路径"""
        path = filedialog.askdirectory()
        if path:
            self.entry_emu_path.delete(0, tk.END)
            self.entry_emu_path.insert(0, path)

    def browse_target_dir(self):
        """浏览目标目录"""
        path = filedialog.askdirectory()
        if path:
            self.entry_target.delete(0, tk.END)
            self.entry_target.insert(0, path)

    def load_preferences(self):
        """加载用户偏好设置"""
        config = configparser.ConfigParser()
        if os.path.exists(self.config_file):
            config.read(self.config_file)

            if config.has_section('ADB'):
                self.adb_port = config.get('ADB', 'port', fallback='7555')
                self.emulator_path = config.get('ADB', 'emulator_path',
                                                fallback='/sdcard/Android/data/cn.com.bookan/files/bookan/magazine')

            self.source_dir = os.path.expanduser(config.get('LOCAL', 'source_dir',
                                                            fallback='~/Documents/magazine_images'))
            self.target_dir = os.path.expanduser(config.get('LOCAL', 'target_dir',
                                                            fallback='~/Documents/Books/magazine_pdfs'))

            # 加载窗口几何信息
            if config.has_section('WINDOW'):
                geometry = config.get('WINDOW', 'geometry', fallback='800x560')
                position = config.get('WINDOW', 'position', fallback=None)

                self.root.geometry(geometry)
                if position:
                    self.root.geometry(
                        f"+{position.split('+')[1]}+{position.split('+')[2]}")
            else:
                self.root.geometry('800x560')

            # 自动创建配置目录
            Path(self.source_dir).mkdir(parents=True, exist_ok=True)
            Path(self.target_dir).mkdir(parents=True, exist_ok=True)

    def save_preferences(self):
        """保存用户偏好设置"""
        config = configparser.ConfigParser()
        config['LOCAL'] = {
            'source_dir': self.source_dir.replace(os.path.expanduser('~'), '~', 1),
            'target_dir': self.target_dir.replace(os.path.expanduser('~'), '~', 1)
        }
        config['ADB'] = {
            'port': self.adb_port,
            'emulator_path': self.entry_emu_path.get()
        }

        # 保存窗口几何信息
        config['WINDOW'] = {
            'geometry': self.root.geometry(),
            'position': self.root.geometry()  # 包含位置信息
        }

        with open(self.config_file, 'w', encoding='utf-8') as configfile:
            config.write(configfile)

    def open_config_dialog(self):
        """打开配置对话框"""
        config_dialog = tk.Toplevel(self.root)
        config_dialog.title('路径配置')
        config_dialog.resizable(False, False)

        # 设置窗口图标
        try:
            config_dialog.iconbitmap('app_icon.ico')
        except tk.TclError:  # 捕获特定的 TclError 异常
            pass

        # 主容器
        main_frame = ttk.Frame(config_dialog, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 源目录配置
        ttk.Label(main_frame, text='源目录:', style='Modern.TLabel').grid(
            row=0, column=0, sticky=tk.W, pady=5)
        entry_source = ModernEntry(main_frame)
        entry_source.insert(0, self.source_dir)
        entry_source.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)

        btn_source = ModernButton(main_frame, text='浏览', command=lambda: entry_source.insert(
            0, filedialog.askdirectory()))
        btn_source.grid(row=0, column=2, pady=5)

        # 目标目录配置
        ttk.Label(main_frame, text='目标目录:', style='Modern.TLabel').grid(
            row=1, column=0, sticky=tk.W, pady=5)
        entry_target = ModernEntry(main_frame)
        entry_target.insert(0, self.target_dir)
        entry_target.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)

        btn_target = ModernButton(main_frame, text='浏览', command=lambda: entry_target.insert(
            0, filedialog.askdirectory()))
        btn_target.grid(row=1, column=2, pady=5)

        # 按钮框架
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=2, column=0, columnspan=3, pady=(10, 0))

        btn_save = ModernButton(btn_frame, text='保存', command=lambda: self.save_config(
            entry_source.get(), entry_target.get(), config_dialog))
        btn_save.pack(side=tk.RIGHT, padx=5)

        btn_cancel = ModernButton(
            btn_frame, text='取消', command=config_dialog.destroy)
        btn_cancel.pack(side=tk.RIGHT, padx=5)

        # 配置网格权重
        main_frame.columnconfigure(1, weight=1)

        # 窗口居中
        self.center_window_on_parent(config_dialog, 500, 200)

    def save_config(self, source_dir, target_dir, dialog):
        """保存配置"""
        self.source_dir = source_dir
        self.target_dir = target_dir
        self.save_preferences()
        dialog.destroy()
        self.update_status("配置已保存")

    def update_status(self, message):
        """更新状态栏"""
        if self.status_bar:
            self.status_bar.config(text=message)
        self.root.update_idletasks()

    def update_progress(self, value):
        """更新进度条"""
        if self.progress_bar:
            self.progress_bar['value'] = value
        self.root.update_idletasks()

    def adb_connect(self):
        """建立ADB连接"""
        try:
            self.update_status("正在连接ADB...")
            self.update_progress(30)

            # 使用CREATE_NO_WINDOW标志防止弹出命令提示符窗口
            result = subprocess.run(
                f'adb connect 127.0.0.1:{self.adb_port}',
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=10,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW  # 不显示命令提示符窗口
            )
            output = result.stdout or ''

            if 'connected' in output:
                self.update_status(f'ADB已连接: {self.adb_port}')
                self.update_progress(100)
            else:
                self.update_status(f'连接失败: {output.strip()}')
                self.update_progress(0)
        except subprocess.TimeoutExpired:
            self.update_status('连接超时')
            self.update_progress(0)
        except subprocess.CalledProcessError as e:
            self.update_status(f'连接错误: {str(e)}')
            self.update_progress(0)
        finally:
            self.root.after(2000, lambda: self.update_progress(0))

    def adb_pull(self):
        """执行ADB文件拉取"""
        self.save_preferences()
        self.update_status("ADB复制启动...")
        self.update_progress(10)

        try:
            # 先建立连接
            self.adb_connect()
            self.update_progress(20)

            # 执行pull命令，使用CREATE_NO_WINDOW标志
            process = subprocess.Popen(
                f'adb pull {self.entry_emu_path.get()} "{self.source_dir}"',
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding='utf-8',
                errors='ignore',
                creationflags=subprocess.CREATE_NO_WINDOW  # 不显示命令提示符窗口
            )

            # 实时更新进度
            progress = 20
            for line in iter(process.stdout.readline, ''):
                self.update_status(line.strip())
                logging.info(line.strip())

                # 模拟进度更新
                progress = min(progress + 5, 90)
                self.update_progress(progress)

            process.wait()
            output = process.stdout.read() or ''

            if process.returncode == 0 and '0 skipped' in output:
                magazine_id = os.path.basename(
                    self.entry_emu_path.get()).split('_')[-1]
                self.update_status(f'文件同步成功: {magazine_id}')
                self.update_progress(100)
            else:
                self.update_status(f'同步失败: {output.strip()}')
                self.update_progress(0)
        except subprocess.CalledProcessError as e:
            self.update_status(f'ADB命令执行失败: {str(e)}')
            self.update_progress(0)
            logging.error('ADB操作失败: %s', str(e))
        finally:
            self.root.after(2000, lambda: self.update_progress(0))

    def process_all(self):
        """处理所有文件"""
        Thread(target=lambda: batch_process(
            self.source_dir,
            self.target_dir,
            self.update_status,
            self.update_progress
        )).start()


def batch_process(source_dir, target_dir, status_callback, progress_callback=None):
    """批量处理监控目录中的杂志文件"""
    check_interval = 30

    while True:
        processed = False
        files = os.listdir(source_dir)
        total_files = len([f for f in files if f.endswith('.txt')])

        for i, filename in enumerate(files):
            if filename.endswith('.txt'):
                magazine_id = filename[:-4]
                try:
                    status_callback(f'正在处理: {magazine_id}')
                    if progress_callback:
                        progress_callback((i / total_files) * 100)

                    main_processor(source_dir, target_dir, magazine_id)
                    processed = True

                    if progress_callback:
                        progress_callback(((i + 1) / total_files) * 100)
                except (OSError, ValueError, IOError) as processing_error:
                    logging.error('处理失败: %s', processing_error)
                    status_callback(f'处理失败: {str(processing_error)}')
                    if progress_callback:
                        progress_callback(0)

        if not processed:
            status_callback('等待新文件...')
            if progress_callback:
                progress_callback(0)
            time.sleep(check_interval)
        else:
            break


def main_processor(source_dir, target_dir, magazine_id):
    """主处理逻辑"""
    config = configparser.ConfigParser()
    config.read('preferences.cfg')

    # 配置路径
    source_dir = config.get('LOCAL', 'source_dir',
                            fallback=os.path.join(os.path.expanduser('~'),
                                                  'Documents',
                                                  'magazine_images'))
    target_dir = config.get('LOCAL', 'target_dir',
                            fallback=os.path.join(os.path.expanduser('~'),
                                                  'Documents',
                                                  'Books',
                                                  'magazines_PDF'))

    # 自动创建目标目录
    Path(source_dir).mkdir(parents=True, exist_ok=True)
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    img_folder = os.path.join(source_dir, magazine_id)

    # 二次路径验证
    if not os.path.exists(img_folder):
        source_dir = config.get('LOCAL', 'default_mumu_source',
                                fallback=os.path.join(os.path.expanduser('~'),
                                                      'Mumu共享文件夹'))
        img_folder = os.path.join(source_dir, magazine_id)

    txt_path = os.path.join(source_dir, f'{magazine_id}.txt')

    # 按TXT顺序重命名文件
    renamed_files = []
    with open(txt_path, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f.readlines()]

    for index, line in enumerate(lines, 1):
        orig_name = line.strip().split('/')[-1]
        orig_path = os.path.join(source_dir, magazine_id, orig_name)
        new_name = f"{index:04d}.jpg"
        new_path = os.path.join(source_dir, magazine_id, new_name)

        if os.path.exists(new_path):
            continue
        if os.path.exists(orig_path):
            os.rename(orig_path, new_path)
            renamed_files.append(new_path)

    # 合成PDF
    pdf_path = os.path.join(target_dir, f"{magazine_id}.pdf")
    with open(pdf_path, "wb") as pdf_file:
        pdf_file.write(img2pdf.convert(sorted(renamed_files)))

    # 清理源文件
    try:
        os.remove(txt_path)
        if os.path.exists(img_folder):
            shutil.rmtree(img_folder)
    except (OSError, shutil.Error) as cleanup_error:
        logging.error("清理文件时出错: %s", cleanup_error)
        print(f"清理文件时出错: {cleanup_error}")


def ui_main():
    """应用程序UI入口函数"""
    # 配置日志
    logging.basicConfig(
        level=logging.ERROR,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('tools.log'),
            logging.StreamHandler()
        ]
    )

    # 创建并运行主窗口
    window_manager = WindowManager()
    window_manager.root.mainloop()


if __name__ == "__main__":
    ui_main()
