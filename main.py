from ultralytics.yolo.engine.predictor import BasePredictor
from ultralytics.yolo.engine.results import Results
from ultralytics.yolo.utils import DEFAULT_CFG, ROOT, LOGGER, SETTINGS, callbacks, ops, colorstr
from ultralytics.yolo.utils.plotting import Annotator, colors, save_one_box
from ultralytics.yolo.utils.torch_utils import select_device, smart_inference_mode
from ultralytics.yolo.utils.files import increment_path
from ultralytics.yolo.utils.checks import check_imgsz, check_imshow
from ultralytics.yolo.cfg import get_cfg
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton,  QPlainTextEdit,QMessageBox, QFileDialog, QMenu
from PySide6.QtGui import QImage, QPixmap, QPainter, QIcon, QAction
from PySide6.QtCore import QTimer, QThread, Signal, QObject, QPoint, Qt
from ui.CustomMessageBox import MessageBox
from ui.home import Ui_MainWindow
from collections import defaultdict
from pathlib import Path
from utils.capnums import Camera
from utils.rtsp_win import Window
import numpy as np
import time
import json
import torch
import sys
import cv2
import os


class YoloPredictor(BasePredictor, QObject):
    yolo2main_pre_img = Signal(np.ndarray)   # 原始图像信号
    yolo2main_res_img = Signal(np.ndarray)   # 检测结果信号
    yolo2main_status_msg = Signal(str)       # 正在检测/暂停/停止/检测结束/错误报告 信号
    yolo2main_fps = Signal(str)              # fps信号
    yolo2main_labels = Signal(dict)          # 检测到的目标结果（各分类数量）
    yolo2main_progress = Signal(int)         # 完成度

    def __init__(self, cfg=DEFAULT_CFG, overrides=None): # 初始化
        super(YoloPredictor, self).__init__()  # 继承父类
        QObject.__init__(self)

        self.args = get_cfg(cfg, overrides)
        project = self.args.project or Path(SETTINGS['runs_dir']) / self.args.task
        name = f'{self.args.mode}'
        self.save_dir = increment_path(Path(project) / name, exist_ok=self.args.exist_ok)
        self.done_warmup = False
        if self.args.show:
            self.args.show = check_imshow(warn=True)

        # GUI args
        self.used_model_name = None      # 使用的检测模型名
        self.new_model_name = None       # 实时改变的模型
        self.source = ''                 # 输入源
        self.stop_dtc = False            # 终止检测
        self.continue_dtc = True         # 是否暂停   
        self.save_res = False            # 保存检测结果
        self.save_txt = False            # 保存txt文件
        self.iou_thres = 0.45            # iou
        self.conf_thres = 0.25           # conf
        self.speed_thres = 10            # 播放延时,单位ms
        self.labels_dict = {}            # 返回结果的字典
        self.progress_value = 0          # 进度条
    

        # Usable if setup is done
        self.model = None
        self.data = self.args.data  # data_dict
        self.imgsz = None
        self.device = None
        self.dataset = None
        self.vid_path, self.vid_writer = None, None
        self.annotator = None
        self.data_path = None
        self.source_type = None
        self.batch = None
        self.callbacks = defaultdict(list, callbacks.default_callbacks)  # add callbacks
        callbacks.add_integration_callbacks(self)

    # main for detect
    @smart_inference_mode()
    def run(self):
        # try:
        if self.args.verbose:
            LOGGER.info('')

        # 设置模型    
        self.yolo2main_status_msg.emit('正在加载模型...')
        if not self.model:
            self.setup_model(self.new_model_name)
            self.used_model_name = self.new_model_name

        # 设置源
        self.setup_source(self.source if self.source is not None else self.args.source)

        # 检查保存路径/label
        if self.save_res or self.save_txt:
            (self.save_dir / 'labels' if self.save_txt else self.save_dir).mkdir(parents=True, exist_ok=True)

        # warmup model
        if not self.done_warmup:
            self.model.warmup(imgsz=(1 if self.model.pt or self.model.triton else self.dataset.bs, 3, *self.imgsz))
            self.done_warmup = True

        self.seen, self.windows, self.dt, self.batch = 0, [], (ops.Profile(), ops.Profile(), ops.Profile()), None

        # 开始检测
        # for batch in self.dataset:


        count = 0                       # 已运行位置
        start_time = time.time()        # 用于计算帧率
        batch = iter(self.dataset)
        while True:
            # 终止检测
            if self.stop_dtc:
                if isinstance(self.vid_writer[-1], cv2.VideoWriter):
                    self.vid_writer[-1].release()  # release final video writer
                self.yolo2main_status_msg.emit('检测完成')
                break
            
            # 中途变更模型
            if self.used_model_name != self.new_model_name:  
                # self.yolo2main_status_msg.emit('正在加载模型...')
                self.setup_model(self.new_model_name)
                self.used_model_name = self.new_model_name
            
            # 暂停开关
            if self.continue_dtc:
                # time.sleep(0.001)
                self.yolo2main_status_msg.emit('检测中...')
                batch = next(self.dataset)  # 下一个数据

                self.batch = batch
                path, im, im0s, vid_cap, s = batch
                visualize = increment_path(self.save_dir / Path(path).stem, mkdir=True) if self.args.visualize else False

                # 计算完成度与帧率  (待优化)
                count += 1              # 帧计数+1
                if vid_cap:
                    all_count = vid_cap.get(cv2.CAP_PROP_FRAME_COUNT)   # 总帧数
                else:
                    all_count = 1
                self.progress_value = int(count/all_count*1000)         # 进度条(0~1000)
                if count % 5 == 0 and count >= 5:                     # 每5帧计算一次计算帧率
                    self.yolo2main_fps.emit('fps:' + str(int(5/(time.time()-start_time))))
                    start_time = time.time()
                
                # preprocess 预处理
                with self.dt[0]:
                    im = self.preprocess(im)
                    if len(im.shape) == 3:
                        im = im[None]  # expand for batch dim
                # inference 推测
                with self.dt[1]:
                    preds = self.model(im, augment=self.args.augment, visualize=visualize)
                # postprocess 后处理
                with self.dt[2]:
                    self.results = self.postprocess(preds, im, im0s)

                # visualize, save, write results  可视化 保存 写入
                n = len(im)     # 待改进：支持多个img
                for i in range(n):
                    self.results[i].speed = {
                        'preprocess': self.dt[0].dt * 1E3 / n,
                        'inference': self.dt[1].dt * 1E3 / n,
                        'postprocess': self.dt[2].dt * 1E3 / n}
                    p, im0 = (path[i], im0s[i].copy()) if self.source_type.webcam or self.source_type.from_img \
                        else (path, im0s.copy())
                    p = Path(p)     # the source dir

                    # s:::   video 1/1 (6/6557) 'path':
                    # must, to get boxs\labels
                    label_str = self.write_results(i, self.results, (p, im, im0))   # labels   /// original :s += 
                    
                    # labels and nums dict
                    self.labels_dict = {}
                    if 'no detections' in label_str:
                        pass
                    else:
                        for i in label_str.split(',')[:-1]:
                            nums, label_name = i.split('~')
                            self.labels_dict[label_name] = int(nums)

                    # save img or video result
                    if self.save_res:
                        self.save_preds(vid_cap, i, str(self.save_dir / p.name))

                    # 发送检测结果
                    self.yolo2main_res_img.emit(im0) # 检测后
                    self.yolo2main_pre_img.emit(im0s if isinstance(im0s, np.ndarray) else im0s[0])   # 检测前
                    self.yolo2main_labels.emit(self.labels_dict)        # webcam need to change the def write_results
                    if self.speed_thres != 0:
                        time.sleep(self.speed_thres/1000)   # 播放延时 spees_thres为ms
                self.yolo2main_progress.emit(self.progress_value)   # 进度

            # 检测完成
            if count + 1 >= all_count:
                if isinstance(self.vid_writer[-1], cv2.VideoWriter):
                    self.vid_writer[-1].release()  # release final video writer
                self.yolo2main_status_msg.emit('检测完成')
                break

    #     # Print results
    #     if self.args.verbose and self.seen:
    #         t = tuple(x.t / self.seen * 1E3 for x in self.dt)  # speeds per image
    #         LOGGER.info(f'Speed: %.1fms preprocess, %.1fms inference, %.1fms postprocess per image at shape '
    #                     f'{(1, 3, *self.imgsz)}' % t)
    #     if self.save_res or self.save_txt or self.args.save_crop:       # 注意save！！！
    #         nl = len(list(self.save_dir.glob('labels/*.txt')))  # number of labels
    #         s = f"\n{nl} label{'s' * (nl > 1)} saved to {self.save_dir / 'labels'}" if self.save_txt else ''
    #         LOGGER.info(f"Results saved to {colorstr('bold', self.save_dir)}{s}")


        # except Exception as e:
            # pass
            # print(e)
            # self.yolo2main_status_msg.emit('%s' % e)


    def get_annotator(self, img):
        return Annotator(img, line_width=self.args.line_thickness, example=str(self.model.names))

    def preprocess(self, img):
        img = torch.from_numpy(img).to(self.model.device)
        img = img.half() if self.model.fp16 else img.float()  # uint8 to fp16/32
        img /= 255  # 0 - 255 to 0.0 - 1.0
        return img

    def postprocess(self, preds, img, orig_img):
        ### important
        preds = ops.non_max_suppression(preds,
                                        self.conf_thres,
                                        self.iou_thres,
                                        agnostic=self.args.agnostic_nms,
                                        max_det=self.args.max_det,
                                        classes=self.args.classes)

        results = []
        for i, pred in enumerate(preds):
            orig_img = orig_img[i] if isinstance(orig_img, list) else orig_img
            shape = orig_img.shape
            pred[:, :4] = ops.scale_boxes(img.shape[2:], pred[:, :4], shape).round()
            path, _, _, _, _ = self.batch
            img_path = path[i] if isinstance(path, list) else path
            results.append(Results(orig_img=orig_img, path=img_path, names=self.model.names, boxes=pred))
        # print(results)
        return results

    def write_results(self, idx, results, batch):
        p, im, im0 = batch
        log_string = ''
        if len(im.shape) == 3:
            im = im[None]  # expand for batch dim
        self.seen += 1
        imc = im0.copy() if self.args.save_crop else im0
        if self.source_type.webcam or self.source_type.from_img:  # batch_size >= 1         # attention
            log_string += f'{idx}: '
            frame = self.dataset.count
        else:
            frame = getattr(self.dataset, 'frame', 0)
        self.data_path = p
        self.txt_path = str(self.save_dir / 'labels' / p.stem) + ('' if self.dataset.mode == 'image' else f'_{frame}')
        # log_string += '%gx%g ' % im.shape[2:]         # !!! don't add img size~
        self.annotator = self.get_annotator(im0)

        det = results[idx].boxes  # TODO: make boxes inherit from tensors

        if len(det) == 0:
            return f'{log_string}(no detections), ' # if no, send this~~

        for c in det.cls.unique():
            n = (det.cls == c).sum()  # detections per class
            log_string += f"{n}~{self.model.names[int(c)]},"   #   {'s' * (n > 1)}, "   # don't add 's'
        # now log_string is the classes 👆


        # write
        for d in reversed(det):
            cls, conf = d.cls.squeeze(), d.conf.squeeze()
            if self.save_txt:  # Write to file
                line = (cls, *(d.xywhn.view(-1).tolist()), conf) \
                    if self.args.save_conf else (cls, *(d.xywhn.view(-1).tolist()))  # label format
                with open(f'{self.txt_path}.txt', 'a') as f:
                    f.write(('%g ' * len(line)).rstrip() % line + '\n')
            if self.save_res or self.args.save_crop or self.args.show or True:  # Add bbox to image(must)
                c = int(cls)  # integer class
                name = f'id:{int(d.id.item())} {self.model.names[c]}' if d.id is not None else self.model.names[c]
                label = None if self.args.hide_labels else (name if self.args.hide_conf else f'{name} {conf:.2f}')
                self.annotator.box_label(d.xyxy.squeeze(), label, color=colors(c, True))
            if self.args.save_crop:
                save_one_box(d.xyxy,
                             imc,
                             file=self.save_dir / 'crops' / self.model.model.names[c] / f'{self.data_path.stem}.jpg',
                             BGR=True)

        return log_string
        


class MainWindow(QMainWindow, Ui_MainWindow):
    main2yolo_begin_sgl = Signal()  # 主窗口向yolo实例发送执行信号
    def __init__(self, parent=None):
        super(MainWindow, self).__init__(parent)  # 继承初始化QMainWindow
        self.setupUi(self)

        # 基本界面
        self.close_button.clicked.connect(self.close)


        # 读取模型文件夹
        self.pt_list = os.listdir('./models')
        self.pt_list = [file for file in self.pt_list if file.endswith('.pt')]  # 筛选pt文件
        self.pt_list.sort(key=lambda x: os.path.getsize('./models/' + x))   # 按文件大小排序
        self.model_box.clear()
        self.model_box.addItems(self.pt_list)
        self.Qtimer_ModelBox = QTimer(self)     # 定时器：每2秒监测模型文件的变动
        self.Qtimer_ModelBox.timeout.connect(self.ModelBoxRefre)
        self.Qtimer_ModelBox.start(2000)

        # Yolo-v8线程
        self.yolo_predict = YoloPredictor()                           # 创建yolo实例
        self.select_model = self.model_box.currentText()                   # 默认模型
        self.yolo_predict.new_model_name = "./models/%s" % self.select_model   # 模型路径
        self.yolo_thread = QThread()                                  # 创建yolo线程
        self.yolo_predict.yolo2main_pre_img.connect(lambda x: self.show_image(x, self.pre_video)) # 绑定原始图
        self.yolo_predict.yolo2main_res_img.connect(lambda x: self.show_image(x, self.res_video)) # 绑定结果图
        self.yolo_predict.yolo2main_status_msg.connect(lambda x: self.show_status(x))             # 绑定状态信息
        self.yolo_predict.yolo2main_fps.connect(lambda x: self.fps_label.setText(x))              # 绑定fps
        self.yolo_predict.yolo2main_labels.connect(self.show_labels)                              # 绑定标签结果
        self.yolo_predict.yolo2main_progress.connect(lambda x: self.progress_bar.setValue(x))     # 绑定进度条
        self.main2yolo_begin_sgl.connect(self.yolo_predict.run)       # 全局信号与实例run函数绑定
        self.yolo_predict.moveToThread(self.yolo_thread)              # 放到创建好的线程中

        # 模型参数
        self.model_box.currentTextChanged.connect(self.change_model)        # 模型选择
        self.iou_spinbox.valueChanged.connect(lambda x:self.change_val(x, 'iou_spinbox'))    # iou box
        self.iou_slider.valueChanged.connect(lambda x:self.change_val(x, 'iou_slider'))      # iou 滚动条
        self.conf_spinbox.valueChanged.connect(lambda x:self.change_val(x, 'conf_spinbox'))  # conf box
        self.conf_slider.valueChanged.connect(lambda x:self.change_val(x, 'conf_slider'))    # conf 滚动条
        self.speed_spinbox.valueChanged.connect(lambda x:self.change_val(x, 'speed_spinbox'))# speed box
        self.speed_slider.valueChanged.connect(lambda x:self.change_val(x, 'speed_slider'))  # speed 滚动条

        
        # 选择检测源
        self.src_file_button.clicked.connect(self.open_src_file)  # 选择本地文件
        self.src_cam_button.clicked.connect(self.chose_cam)   # 选择摄像头
        self.src_rtsp_button.clicked.connect(self.chose_rtsp)  # 选择网络源

        # 设置模型启动按钮
        self.run_button.clicked.connect(self.run_or_continue)   # 暂停/开始
        self.stop_button.clicked.connect(self.stop)             # 终止

        # 其他功能按钮
        self.save_res_button.toggled.connect(self.is_save_res)  # 保存图像选项
        self.save_txt_button.toggled.connect(self.is_save_txt)  # 保存label选项

        self.load_config()

    # 主窗口显示原图与检测结果
    @staticmethod
    def show_image(img_src, label):
        try:
            ih, iw, _ = img_src.shape
            w = label.geometry().width()
            h = label.geometry().height()
            # 保持原始数据比例
            if iw/w > ih/h:
                scal = w / iw
                nw = w
                nh = int(scal * ih)
                img_src_ = cv2.resize(img_src, (nw, nh))

            else:
                scal = h / ih
                nw = int(scal * iw)
                nh = h
                img_src_ = cv2.resize(img_src, (nw, nh))

            frame = cv2.cvtColor(img_src_, cv2.COLOR_BGR2RGB)
            img = QImage(frame.data, frame.shape[1], frame.shape[0], frame.shape[2] * frame.shape[1],
                         QImage.Format_RGB888)
            label.setPixmap(QPixmap.fromImage(img))

        except Exception as e:
            print(repr(e))

    # 控制开始/暂停
    def run_or_continue(self):
        if self.yolo_predict.source == '':
            self.show_status('请先选择视频源后再开始检测....')
            self.run_button.setChecked(False)
        else:
            self.yolo_predict.stop_dtc = False
            if self.run_button.isChecked():
                self.run_button.setChecked(True)    # 开始键
                self.run_button.setText('暂停检测')
                self.save_txt_button.setEnabled(False)  # 开始检测后禁止再勾选保存
                self.save_res_button.setEnabled(False)
                self.show_status('检测中...')           
                self.yolo_predict.continue_dtc = True   # 控制Yolo是否暂停
                if not self.yolo_thread.isRunning():
                    self.yolo_thread.start()
                    self.main2yolo_begin_sgl.emit()

            else:
                self.yolo_predict.continue_dtc = False
                self.show_status("已暂停...")
                self.run_button.setChecked(False)    # 开始键
                self.run_button.setText('继续检测')

    # 底部状态栏信息
    def show_status(self, msg):
        self.status_bar.setText(msg)
        if msg == 'Finished' or msg == '检测完成':
            self.save_res_button.setEnabled(True)
            self.save_txt_button.setEnabled(True)
            self.run_button.setChecked(False)    # 开始键
            self.run_button.setText('开始检测')
            self.progress_bar.setValue(0)
            if self.yolo_thread.isRunning():
                self.yolo_thread.quit()         # 结束进程
            # self.pre_video.clear()           # 清空图像显示   不清，防止检测单张图片不显示
            # self.res_video.clear()           # 清空图像显示

    # 选择本地文件
    def open_src_file(self):
        config_file = 'config/fold.json'    # 默认配置文件
        config = json.load(open(config_file, 'r', encoding='utf-8'))
        open_fold = config['open_fold']     # 选择配置文件的路径
        if not os.path.exists(open_fold):
            open_fold = os.getcwd()
        name, _ = QFileDialog.getOpenFileName(self, 'Video/image', open_fold, "Pic File(*.mp4 *.mkv *.avi *.flv *.jpg *.png)")
        if name:
            self.yolo_predict.source = name
            self.show_status('加载文件：{}'.format(os.path.basename(name))) # 状态栏提示
            config['open_fold'] = os.path.dirname(name)
            config_json = json.dumps(config, ensure_ascii=False, indent=2)  # 写入json，下次打开本次相同路径
            with open(config_file, 'w', encoding='utf-8') as f:
                f.write(config_json)
            self.stop()             # 重新选择文件后就停止检测

    # 选择摄像头源----  have one bug
    def chose_cam(self):
        try:
            self.stop()
            MessageBox(
                self.close_button, title='提示', text='加载摄像头中...', time=2000, auto=True).exec()
            # get the number of local cameras
            _, cams = Camera().get_cam_num()
            popMenu = QMenu()
            popMenu.setFixedWidth(self.src_cam_button.width())
            popMenu.setStyleSheet('''
                                            QMenu {
                                            font-size: 16px;
                                            font-family: "Microsoft YaHei UI";
                                            font-weight: light;
                                            color:white;
                                            padding-left: 5px;
                                            padding-right: 5px;
                                            padding-top: 4px;
                                            padding-bottom: 4px;
                                            border-style: solid;
                                            border-width: 0px;
                                            border-color: rgba(255, 255, 255, 255);
                                            border-radius: 3px;
                                            background-color: rgba(200, 200, 200,50);}
                                            ''')

            for cam in cams:
                exec("action_%s = QAction('%s')" % (cam, cam))
                exec("popMenu.addAction(action_%s)" % cam)

            x = self.src_cam_button.mapToGlobal(self.src_cam_button.pos()).x()      # 1 groupBox_5  弹出-居中
            y = self.src_cam_button.mapToGlobal(self.src_cam_button.pos()).y()      # 1 groupBox_5  弹出-居中
            y = y + self.src_cam_button.frameGeometry().height()
            pos = QPoint(x, y)
            action = popMenu.exec(pos)
            if action:
                self.yolo_predict.source = action.text()
                self.show_status('Loading camera：{}'.format(action.text()))

        except Exception as e:
            self.show_status('%s' % e)

    # 选择网络源
    def chose_rtsp(self):
        self.rtsp_window = Window()
        config_file = 'config/ip.json'
        if not os.path.exists(config_file):
            ip = "rtsp://admin:admin888@192.168.1.2:555"
            new_config = {"ip": ip}
            new_json = json.dumps(new_config, ensure_ascii=False, indent=2)
            with open(config_file, 'w', encoding='utf-8') as f:
                f.write(new_json)
        else:
            config = json.load(open(config_file, 'r', encoding='utf-8'))
            ip = config['ip']
        self.rtsp_window.rtspEdit.setText(ip)
        self.rtsp_window.show()
        self.rtsp_window.rtspButton.clicked.connect(lambda: self.load_rtsp(self.rtsp_window.rtspEdit.text()))
    
    # 加载网络源
    def load_rtsp(self, ip):
        try:
            self.stop()
            MessageBox(
                self.close_button, title='提示', text='加载 rtsp...', time=1000, auto=True).exec()
            self.yolo_predict.source = ip
            new_config = {"ip": ip}
            new_json = json.dumps(new_config, ensure_ascii=False, indent=2)
            with open('config/ip.json', 'w', encoding='utf-8') as f:
                f.write(new_json)
            self.show_status('Loading rtsp：{}'.format(ip))
            self.rtsp_window.close()
        except Exception as e:
            self.show_status('%s' % e)

    # 保存检测结果按钮--图片/视频
    def is_save_res(self):
        if self.save_res_button.checkState() == Qt.CheckState.Unchecked:
            self.show_status('注意：不保存运行图像结果')
            self.yolo_predict.save_res = False
        elif self.save_res_button.checkState() == Qt.CheckState.Checked:
            self.show_status('注意：运行图像结果将保存')
            self.yolo_predict.save_res = True
    
    # 保存检测结果按钮--标签(txt)
    def is_save_txt(self):
        if self.save_txt_button.checkState() == Qt.CheckState.Unchecked:
            self.show_status('注意：不保存标签结果')
            self.yolo_predict.save_txt = False
        elif self.save_txt_button.checkState() == Qt.CheckState.Checked:
            self.show_status('注意：标签结果将保存')
            self.yolo_predict.save_txt = True

    # 配置初始化  ~~~wait to change~~~
    def load_config(self):
        config_file = 'config/setting.json'
        if not os.path.exists(config_file):
            iou = 0.26
            conf = 0.33     # 置信度
            rate = 10
            check = 0
            save_res = 0    # 保存图像
            save_txt = 0    # 保存txt
            new_config = {"iou": iou,
                          "conf": conf,
                          "rate": rate,
                          "check": check,
                          "save_res": save_res,
                          "save_txt": save_txt
                          }
            new_json = json.dumps(new_config, ensure_ascii=False, indent=2)
            with open(config_file, 'w', encoding='utf-8') as f:
                f.write(new_json)
        else:
            config = json.load(open(config_file, 'r', encoding='utf-8'))
            if len(config) != 5:
                iou = 0.26
                conf = 0.33
                rate = 10
                check = 0
                save_res = 0
                save_txt = 0
            else:
                iou = config['iou']
                conf = config['conf']
                rate = config['rate']
                check = config['check']
                save_res = config['save_res']
                save_txt = config['save_txt']
        self.save_res_button.setCheckState(Qt.CheckState(save_res)) # 保存-默认取消勾选
        self.yolo_predict.save_res = False
        self.save_txt_button.setCheckState(Qt.CheckState(save_txt)) # 保存-默认取消勾选
        self.yolo_predict.save_txt = False
        self.run_button.setChecked(False)    # 开始键初始化
        self.run_button.setText('开始检测')         # 文字

    # 终止按钮及关联状态
    def stop(self):
        if self.yolo_thread.isRunning():
            self.yolo_thread.quit()         # 结束进程
        self.yolo_predict.stop_dtc = True
        self.run_button.setChecked(False)    # 开始键恢复
        self.run_button.setText('开始检测')   # 文字
        self.save_res_button.setEnabled(True)   # 能够使用保存按钮
        self.save_txt_button.setEnabled(True)   # 能够使用保存按钮
        self.pre_video.clear()           # 清空图像显示
        self.res_video.clear()           # 清空图像显示
        self.progress_bar.setValue(0)
        self.result_label.clear()

    # 改变检测参数
    def change_val(self, x, flag):
        if flag == 'iou_spinbox':
            self.iou_slider.setValue(int(x*100))    # box值变化，改变slider
        elif flag == 'iou_slider':
            self.iou_spinbox.setValue(x/100)        # slider值变化，改变box
            self.show_status('IOU阈值: %s' % str(x/100))
            self.yolo_predict.iou_thres = x/100
        elif flag == 'conf_spinbox':
            self.conf_slider.setValue(int(x*100))
        elif flag == 'conf_slider':
            self.conf_spinbox.setValue(x/100)
            self.show_status('Conf阈值: %s' % str(x/100))
            self.yolo_predict.conf_thres = x/100
        elif flag == 'speed_spinbox':
            self.speed_slider.setValue(x)
        elif flag == 'speed_slider':
            self.speed_spinbox.setValue(x)
            self.show_status('播放延时: %s 毫秒' % str(x))
            self.yolo_predict.speed_thres = x  # 单位是ms
            
    # 改变模型
    def change_model(self,x):
        self.select_model = self.model_box.currentText()
        self.yolo_predict.new_model_name = "./models/%s" % self.select_model
        self.show_status('模型改变：%s' % self.select_model)

    # 标签结果
    def show_labels(self, labels_dic):
        try:
            self.result_label.clear()
            labels_dic = sorted(labels_dic.items(), key=lambda x: x[1], reverse=True)
            labels_dic = [i for i in labels_dic if i[1]>0]
            result = [' '+str(i[0]) + '：' + str(i[1]) for i in labels_dic]
            self.result_label.addItems(result)
        except Exception as e:
            self.show_status(e)

    # 循环监测模型文件变动
    def ModelBoxRefre(self):
        pt_list = os.listdir('./models')
        pt_list = [file for file in pt_list if file.endswith('.pt')]
        pt_list.sort(key=lambda x: os.path.getsize('./models/' + x))
        # 必须排完序以后再比较，不然一直刷新列表
        if pt_list != self.pt_list:
            self.pt_list = pt_list
            self.model_box.clear()
            self.model_box.addItems(self.pt_list)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    Home = MainWindow()
    Home.show()
    sys.exit(app.exec())      # 退出线程，回到父线程，确保主循环安全退出
