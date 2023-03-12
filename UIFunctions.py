from main import *
from custom_grips import CustomGrip
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QEvent, QTimer
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtWidgets import *
import time

GLOBAL_STATE = False    # max min flag
GLOBAL_TITLE_BAR = True


class UIFuncitons(MainWindow):
    # Expand left menu
    def toggleMenu(self, enable):
        if enable:
            standard = 68
            maxExtend = 180
            width = self.LeftMenuBg.width()

            if width == 68:
                widthExtended = maxExtend
            else:
                widthExtended = standard
            
            # animation
            self.animation = QPropertyAnimation(self.LeftMenuBg, b"minimumWidth")
            self.animation.setDuration(500) # ms
            self.animation.setStartValue(width)
            self.animation.setEndValue(widthExtended)
            self.animation.setEasingCurve(QEasingCurve.InOutQuint)
            self.animation.start()

    # Expand the settings menu on the right
    def settingBox(self, enable):
        if enable:
            # GET WIDTH
            widthRightBox = self.prm_page.width()           # right set column width
            widthLeftBox = self.LeftMenuBg.width()  # left column length
            maxExtend = 220
            standard = 0

            # SET MAX WIDTH
            if widthRightBox == 0:
                widthExtended = maxExtend
            else:
                widthExtended = standard

            # ANIMATION LEFT BOX        
            self.left_box = QPropertyAnimation(self.LeftMenuBg, b"minimumWidth")
            self.left_box.setDuration(500)
            self.left_box.setStartValue(widthLeftBox)
            self.left_box.setEndValue(68)
            self.left_box.setEasingCurve(QEasingCurve.InOutQuart)

            # ANIMATION RIGHT BOX        
            self.right_box = QPropertyAnimation(self.prm_page, b"minimumWidth")
            self.right_box.setDuration(500)
            self.right_box.setStartValue(widthRightBox)
            self.right_box.setEndValue(widthExtended)
            self.right_box.setEasingCurve(QEasingCurve.InOutQuart)

            # GROUP ANIMATION
            self.group = QParallelAnimationGroup()
            self.group.addAnimation(self.left_box)
            self.group.addAnimation(self.right_box)
            self.group.start()

    # maximize window
    def maximize_restore(self):
        global GLOBAL_STATE
        status = GLOBAL_STATE
        if status == False: 
            GLOBAL_STATE = True
            self.showMaximized()    # maximize
            # self.Main_QW.setContentsMargins(0, 0, 0, 0)   # appMargins = Main_QW
            self.max_sf.setToolTip("Restore")
            self.frame_size_grip.hide()         # Hide the interface size adjustment button
            self.left_grip.hide()               # Up, down, left, and right adjustments are prohibited
            self.right_grip.hide()
            self.top_grip.hide()
            self.bottom_grip.hide()
        else:
            GLOBAL_STATE = False
            self.showNormal()       # minimize
            self.resize(self.width()+1, self.height()+1)
            # self.Main_QW.setContentsMargins(10, 10, 10, 10)
            self.max_sf.setToolTip("Maximize")
            self.frame_size_grip.show()
            self.left_grip.show()
            self.right_grip.show()
            self.top_grip.show()
            self.bottom_grip.show()
    
    # window control
    def uiDefinitions(self):
        # Double-click the title bar to maximize
        def dobleClickMaximizeRestore(event):
            if event.type() == QEvent.MouseButtonDblClick:
                QTimer.singleShot(250, lambda: UIFuncitons.maximize_restore(self))
        self.top.mouseDoubleClickEvent = dobleClickMaximizeRestore
        
        # MOVE WINDOW / MAXIMIZE / RESTORE
        def moveWindow(event):
            if GLOBAL_STATE:                        # IF MAXIMIZED CHANGE TO NORMAL
                UIFuncitons.maximize_restore(self)
            if event.buttons() == Qt.LeftButton:    # MOVE
                self.move(self.pos() + event.globalPos() - self.dragPos)
                self.dragPos = event.globalPos()
        self.top.mouseMoveEvent = moveWindow
        # CUSTOM GRIPS
        self.left_grip = CustomGrip(self, Qt.LeftEdge, True)
        self.right_grip = CustomGrip(self, Qt.RightEdge, True)
        self.top_grip = CustomGrip(self, Qt.TopEdge, True)
        self.bottom_grip = CustomGrip(self, Qt.BottomEdge, True)

        # MINIMIZE
        self.min_sf.clicked.connect(lambda: self.showMinimized())
        # MAXIMIZE/RESTORE
        self.max_sf.clicked.connect(lambda: UIFuncitons.maximize_restore(self))
        # CLOSE APPLICATION
        self.close_button.clicked.connect(self.close)

    # Control the stretching of the four sides of the window
    def resize_grips(self):
        self.left_grip.setGeometry(0, 10, 10, self.height())
        self.right_grip.setGeometry(self.width() - 10, 10, 10, self.height())
        self.top_grip.setGeometry(0, 0, self.width(), 10)
        self.bottom_grip.setGeometry(0, self.height() - 10, self.width(), 10)

    # Show module to add shadow
    def shadow_style(self, widget, Color):
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setOffset(8, 8)  # offset
        shadow.setBlurRadius(38)    # shadow radius
        shadow.setColor(Color)    # shadow color
        widget.setGraphicsEffect(shadow) 