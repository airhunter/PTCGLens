"""同一用户数据目录只运行一份浮卡；重复启动显示状态，也可请求退出。"""

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name(data_dir):
    identity = os.path.normcase(str(Path(data_dir).resolve()))
    return "ptcglens-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def send_command(data_dir, command, timeout=1000):
    if command not in ("show", "quit"):
        raise ValueError("未知的程序控制命令")
    socket = QLocalSocket()
    socket.connectToServer(server_name(data_dir))
    if not socket.waitForConnected(timeout):
        return False
    socket.write((command + "\n").encode("ascii"))
    socket.flush()
    if not socket.bytesAvailable() and not socket.waitForReadyRead(timeout):
        socket.abort()
        return False
    accepted = bytes(socket.readAll()) == b"ok\n"
    socket.disconnectFromServer()
    return accepted


class AppInstance(QObject):
    show_requested = Signal()
    quit_requested = Signal()

    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.lock = QLockFile(str(self.data_dir / "overlay.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self.accept_connections)
        self.clients = set()

    def start(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.lock.tryLock(0):
            return False
        name = server_name(self.data_dir)
        # 只有持有该数据目录锁的进程才能清理崩溃遗留 socket。
        QLocalServer.removeServer(name)
        if not self.server.listen(name):
            self.lock.unlock()
            raise OSError("无法建立程序控制入口：" + self.server.errorString())
        return True

    def accept_connections(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.clients.add(socket)
            socket.disconnected.connect(lambda s=socket: self.release(s))
            socket.readyRead.connect(lambda s=socket: self.read_command(s))
            QTimer.singleShot(2000, socket, socket.disconnectFromServer)
            self.read_command(socket)

    def read_command(self, socket):
        if socket.bytesAvailable() > 64:
            socket.abort()
            return
        if not socket.canReadLine():
            return
        command = bytes(socket.readLine()).strip()
        signal = {b"show": self.show_requested, b"quit": self.quit_requested}.get(command)
        socket.write(b"ok\n" if signal is not None else b"error\n")
        socket.flush()
        socket.disconnectFromServer()
        if signal is not None:
            QTimer.singleShot(0, signal.emit)

    def release(self, socket):
        self.clients.discard(socket)
        socket.deleteLater()

    def close(self):
        self.server.close()
        for socket in tuple(self.clients):
            socket.abort()
        self.lock.unlock()
