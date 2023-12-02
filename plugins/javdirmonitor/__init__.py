import datetime
import re
import shutil
import threading
import traceback
from enum import Enum
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app import schemas
from app.chain.tmdb import TmdbChain
from app.chain.transfer import TransferChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.core.metainfo import MetaInfoPath
from app.core.meta import MetaVideo, MetaBase
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import Notification, NotificationType, TransferInfo
from app.schemas.types import EventType, MediaType, SystemConfigKey
from app.utils.string import StringUtils
from app.utils.system import SystemUtils
from .javbus import JavbusWeb
from .javlib import JavlibWeb
from .javfiletransfer import JavFileTransferModule
from .javscraper import JavScraper

lock = threading.Lock()


class JavMediaType(Enum):
    JAV = 'JAV'

class FileMonitorHandler(FileSystemEventHandler):
    """
    目录监控响应类
    """

    def __init__(self, monpath: str, sync: Any, **kwargs):
        super(FileMonitorHandler, self).__init__(**kwargs)
        self._watch_path = monpath
        self.sync = sync

    def on_created(self, event):
        self.sync.event_handler(event=event, text="创建",
                                mon_path=self._watch_path, event_path=event.src_path)

    def on_moved(self, event):
        self.sync.event_handler(event=event, text="移动",
                                mon_path=self._watch_path, event_path=event.dest_path)


class JavDirMonitor(_PluginBase):
    # 插件名称
    plugin_name = "Jav目录监控"
    # 插件描述
    plugin_desc = "监控目录文件发生变化时实时整理到媒体库。"
    # 插件图标
    plugin_icon = "directory.png"
    # 主题色
    plugin_color = "#E0995E"
    # 插件版本
    plugin_version = "1.9"
    # 插件作者
    plugin_author = "boji"
    # 作者主页
    author_url = "https://github.com/"
    # 插件配置项ID前缀
    plugin_config_prefix = "jav_dirmonitor_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _scheduler = None
    transferhis = None
    downloadhis = None
    transferchian = None
    tmdbchain = None
    _observer = []
    _enabled = False
    _notify = False
    _onlyonce = False
    _cron = None
    _scrap_metadata = True
    # 模式 compatibility/fast
    _mode = "fast"
    # 转移方式
    _transfer_type = settings.TRANSFER_TYPE
    _monitor_dirs = ""
    _exclude_keywords = ""
    DEFAULT_RENAME_FORMAT = "{{actor}}/{{year}} {{title}}/{{code}}{% if cn_subtitle %}{{cn_subtitle}}{% endif %}{{fileExt}}"
    _rename_format = ""
    _onlyonce_path = ""
    _interval: int = 10
    # 存储源目录与目的目录关系
    _dirconf: Dict[str, Optional[Path]] = {}
    # 存储源目录转移方式
    _transferconf: Dict[str, Optional[str]] = {}
    _medias = {}
    # 退出事件
    _event = threading.Event()

    def init_plugin(self, config: dict = None):
        self.transferhis = TransferHistoryOper()
        self.downloadhis = DownloadHistoryOper()
        self.transferchian = TransferChain()
        self.tmdbchain = TmdbChain()
        self.javbus = JavbusWeb()
        self.javlib = JavlibWeb()
        self.jav_file_transfer = JavFileTransferModule()
        self.jav_scraper = JavScraper()
        # 清空配置
        self._dirconf = {}
        self._transferconf = {}

        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._notify = config.get("notify")
            self._onlyonce = config.get("onlyonce")
            self._mode = config.get("mode")
            self._transfer_type = config.get("transfer_type")
            self._monitor_dirs = config.get("monitor_dirs") or ""
            self._exclude_keywords = config.get("exclude_keywords") or ""
            self._interval = config.get("interval") or 10
            self._cron = config.get("cron")
            self._scrap_metadata = config.get("scrap_metadata")
            self._rename_format = config.get("rename_format") or self.DEFAULT_RENAME_FORMAT
            self._onlyonce_path = config.get("onlyonce_path") or ""

        # 停止现有任务
        self.stop_service()

        if self._enabled or self._onlyonce:
            # 定时服务管理器
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            # 追加入库消息统一发送服务
            self._scheduler.add_job(self.send_msg, trigger='interval', seconds=15)

            # 读取目录配置
            monitor_dirs = self._monitor_dirs.split("\n")
            if not monitor_dirs:
                return

            for mon_path in monitor_dirs:
                # 格式源目录:目的目录
                if not mon_path:
                    continue

                # 自定义转移方式
                _transfer_type = self._transfer_type
                if mon_path.count("#") == 1:
                    _transfer_type = mon_path.split("#")[1]
                    mon_path = mon_path.split("#")[0]

                # 存储目的目录
                if SystemUtils.is_windows():
                    if mon_path.count(":") > 1:
                        paths = [mon_path.split(":")[0] + ":" + mon_path.split(":")[1],
                                 mon_path.split(":")[2] + ":" + mon_path.split(":")[3]]
                    else:
                        paths = [mon_path]
                else:
                    paths = mon_path.split(":")

                # 目的目录
                target_path = None
                if len(paths) > 1:
                    mon_path = paths[0]
                    target_path = Path(paths[1])
                    self._dirconf[mon_path] = target_path
                else:
                    self._dirconf[mon_path] = None

                # 转移方式
                self._transferconf[mon_path] = _transfer_type

                # 启用目录监控
                if self._enabled:
                    # 检查媒体库目录是不是下载目录的子目录
                    try:
                        if target_path and target_path.is_relative_to(Path(mon_path)):
                            logger.warn(f"{target_path} 是下载目录 {mon_path} 的子目录，无法监控")
                            self.systemmessage.put(f"{target_path} 是下载目录 {mon_path} 的子目录，无法监控")
                            continue
                    except Exception as e:
                        logger.debug(str(e))
                        pass

                    try:
                        if self._mode == "compatibility":
                            # 兼容模式，目录同步性能降低且NAS不能休眠，但可以兼容挂载的远程共享目录如SMB
                            observer = PollingObserver(timeout=10)
                        else:
                            # 内部处理系统操作类型选择最优解
                            observer = Observer(timeout=10)
                        self._observer.append(observer)
                        observer.schedule(FileMonitorHandler(mon_path, self), path=mon_path, recursive=True)
                        observer.daemon = True
                        observer.start()
                        logger.info(f"{mon_path} 的目录监控服务启动")
                    except Exception as e:
                        err_msg = str(e)
                        if "inotify" in err_msg and "reached" in err_msg:
                            logger.warn(
                                f"目录监控服务启动出现异常：{err_msg}，请在宿主机上（不是docker容器内）执行以下命令并重启："
                                + """
                                     echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
                                     echo fs.inotify.max_user_instances=524288 | sudo tee -a /etc/sysctl.conf
                                     sudo sysctl -p
                                     """)
                        else:
                            logger.error(f"{mon_path} 启动目录监控失败：{err_msg}")
                        self.systemmessage.put(f"{mon_path} 启动目录监控失败：{err_msg}")

            # 运行一次定时服务
            if self._onlyonce:
                if len(self._onlyonce_path) == 0:
                    logger.info("目录监控服务启动，立即运行一次")
                    self._scheduler.add_job(func=self.sync_all, trigger='date',
                                            run_date=datetime.datetime.now(
                                                tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                            )
                    # 关闭一次性开关
                    self._onlyonce = False
                    self._onlyonce_path = ""
                    # 保存配置
                    self.__update_config()

                else:
                    logger.info("目录监控服务启动，立即运行指定目录一次")
                    self._scheduler.add_job(func=self.sync_onlyonce_path, trigger='date',
                                            run_date=datetime.datetime.now(
                                                tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                            )
                    

            # 全量同步定时
            if self._enabled and self._cron:
                try:
                    self._scheduler.add_job(func=self.sync_all,
                                            trigger=CronTrigger.from_crontab(self._cron),
                                            name="目录监控全量同步")
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")
                    # 推送实时消息
                    self.systemmessage.put(f"执行周期配置错误：{str(err)}")

            # 启动定时服务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "onlyonce": self._onlyonce,
            "mode": self._mode,
            "transfer_type": self._transfer_type,
            "monitor_dirs": self._monitor_dirs,
            "exclude_keywords": self._exclude_keywords,
            "interval": self._interval,
            "cron": self._cron,
            "scrap_metadata": self._scrap_metadata,
            "rename_format": self._rename_format,
            "onlyonce_path": self._onlyonce_path,
        })

    @eventmanager.register(EventType.PluginAction)
    def remote_sync(self, event: Event):
        """
        远程全量同步
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "directory_sync":
                return
            self.post_message(channel=event.event_data.get("channel"),
                              title="开始同步监控目录 ...",
                              userid=event.event_data.get("user"))
        self.sync_all()
        if event:
            self.post_message(channel=event.event_data.get("channel"),
                              title="监控目录同步完成！", userid=event.event_data.get("user"))

    def sync_all(self):
        """
        立即运行一次，全量同步目录中所有文件
        """
        logger.info("开始全量同步监控目录 ...")
        # 遍历所有监控目录
        for mon_path in self._dirconf.keys():
            # 遍历目录下所有文件
            for file_path in SystemUtils.list_files(Path(mon_path), settings.RMT_MEDIAEXT):
                self.__handle_file(event_path=str(file_path), mon_path=mon_path)
        logger.info("全量同步监控目录完成！")
        
    def sync_onlyonce_path(self):
        """
        立即运行一次，同步指定目录
        """
        if len(self._onlyonce_path) == 0:
            return

        mon_path = self._onlyonce_path.split(":")[0]
        self._dirconf[mon_path] = Path(self._onlyonce_path.split("#")[0].split(":")[1])
        self._transferconf[mon_path] = self._onlyonce_path.split("#")[1] if len(self._onlyonce_path.split("#")) == 1 else self._transfer_type


        # 关闭一次性开关
        self._onlyonce = False
        self._onlyonce_path = ""
        # 保存配置
        self.__update_config()

        logger.info(f"立即开始同步指定目录 {mon_path}")
        self.__handle_file(event_path=str(mon_path), mon_path=mon_path)
        logger.info("监控指定目录完成！")

    def event_handler(self, event, mon_path: str, text: str, event_path: str):
        """
        处理文件变化
        :param event: 事件
        :param mon_path: 监控目录
        :param text: 事件描述
        :param event_path: 事件文件路径
        """
        if not event.is_directory:
            # 文件发生变化
            logger.debug("文件%s：%s" % (text, event_path))
            self.__handle_file(event_path=event_path, mon_path=mon_path)

    def __get_jav_detail(self, id):
        """
        根据jav ID返回jav详情，带休眠
        """
            
        jav_info = self.javbus.detail(id)

        jav_info['date'] = (jav_info.get('date', None) or '').replace('-', '.')
        jav_info['backdrop_img'] = jav_info.get('img', None) or ''
        jav_info['post_img'] = jav_info.get('img', None) or ''
        if jav_info.get('img'):
            jav_info['post_img'] = (jav_info.get('img') or '').replace('cover', 'thumb').replace('_b.jpg', '.jpg')
            
        if not jav_info:
            logger.warn("【Javbus】%s 未找到Jav详细信息" % id)
            return None
        if not jav_info.get("title"):
            logger.warn("【Javbus】%s 未找到Jav详细信息" % id)
            return None
        logger.info("【Javbus】查询到数据：%s" % jav_info.get("title"))
        
        
        # 去javlib获取评分
        logger.info("【Javlib】正在通过Javlib API查询Jav详情：%s" % id)
        javlib_info = self.javlib.detail_by_javid(id)
        if javlib_info:
            logger.info("【Javlib】查询到数据：%s, 评分：%s" % (javlib_info.get("id"), str(javlib_info.get('rating'))))
            jav_info['rating'] = javlib_info.get('rating')
            jav_info['javlib_id'] = javlib_info.get('vid')
            jav_info['javlib_info'] = javlib_info
        else:
            jav_info['rating'] = 0.0
            jav_info['javlib_id'] = ''
            jav_info['javlib_info'] = {}
        return jav_info

    def __recognize_media(self, file_path):
        # 元数据(只能拿到番号)
        file_meta = self.JavMetaInfoPath(file_path)
        if not file_meta.doubanid:
            return None, None
        # 识别媒体信息
        # mediainfo: MediaInfo = self.chain.recognize_media(meta=file_meta,
        #                                                   tmdbid=download_history.tmdbid if download_history else None)
        jav_info = self.__get_jav_detail(file_meta.doubanid)
        if jav_info is None:
            return file_meta, None

        file_meta.year = jav_info.get('date', None) or ''
        file_meta.cn_name = jav_info.get('title', None) or file_meta.doubanid

        mediainfo: MediaInfo = MediaInfo()
        mediainfo.type = JavMediaType.JAV
        mediainfo.title = jav_info.get('title', None) or file_meta.doubanid
        mediainfo.year = jav_info.get('date', None) or ''
        mediainfo.douban_id = jav_info.get('id', None) or file_meta.doubanid
        mediainfo.original_title = jav_info.get('title', None) or file_meta.doubanid
        mediainfo.release_date = jav_info.get('date', None) or ''
        # mediainfo.backdrop_path = jav_info.get('backdrop_img', None) or ''
        mediainfo.background_path = jav_info.get('backdrop_img', None) or ''
        mediainfo.poster_path = jav_info.get('backdrop_img', None) or ''
        mediainfo.poster_thumb_path = jav_info.get('post_img', None) or ''
        samples = []
        for i, sample in enumerate(jav_info.get("samples", None) or []):
            if i < 30 and "src" in sample and "http" in sample['src']:
                mediainfo.__setattr__(f"sample{i+1}_path", sample['src'])
                samples.append(sample['src'])
        mediainfo.samples = samples
        mediainfo.vote_average = jav_info.get('rating', None) or 0
        mediainfo.actors = jav_info.get('stars', None) or [{"starId": "-1", "starName": "unknown"}]
        mediainfo.directors = [jav_info.get('director', None)]
        mediainfo.genres = jav_info.get('tags', None) or []
        mediainfo.adult = True
        mediainfo.producer = jav_info.get('producer', None) or {'producerId': '-1', 'producerName': 'unknown'}
        mediainfo.publisher = jav_info.get('publisher', None) or {'publisherId': '-1', 'publisherName': 'unknown'}
        mediainfo.cn_subtitle = file_meta.cn_subtitle
        mediainfo.jav_info = jav_info

        return file_meta, mediainfo
        
        
    def __handle_file(self, event_path: str, mon_path: str):
        """
        同步一个文件
        :param event_path: 事件文件路径
        :param mon_path: 监控目录
        """
        file_path = Path(event_path)
        try:
            if not file_path.exists():
                return
            # 全程加锁
            with lock:
                transfer_history = self.transferhis.get_by_src(event_path)
                if transfer_history:
                    logger.debug("文件已处理过：%s" % event_path)
                    return

                # 回收站及隐藏的文件不处理
                if event_path.find('/@Recycle/') != -1 \
                        or event_path.find('/#recycle/') != -1 \
                        or event_path.find('/.') != -1 \
                        or event_path.find('/@eaDir') != -1:
                    logger.debug(f"{event_path} 是回收站或隐藏的文件")
                    return

                # 命中过滤关键字不处理
                if self._exclude_keywords:
                    for keyword in self._exclude_keywords.split("\n"):
                        if keyword and re.findall(keyword, event_path):
                            logger.info(f"{event_path} 命中过滤关键字 {keyword}，不处理")
                            return

                # 整理屏蔽词不处理
                transfer_exclude_words = self.systemconfig.get(SystemConfigKey.TransferExcludeWords)
                if transfer_exclude_words:
                    for keyword in transfer_exclude_words:
                        if not keyword:
                            continue
                        if keyword and re.search(r"%s" % keyword, event_path, re.IGNORECASE):
                            logger.info(f"{event_path} 命中整理屏蔽词 {keyword}，不处理")
                            return

                # 不是媒体文件不处理
                if file_path.suffix not in settings.RMT_MEDIAEXT:
                    logger.debug(f"{event_path} 不是媒体文件")
                    return

                # 判断是不是蓝光目录
                if re.search(r"BDMV[/\\]STREAM", event_path, re.IGNORECASE):
                    # # 截取BDMV前面的路径
                    # event_path = event_path[:event_path.find("BDMV")]
                    # file_path = Path(event_path)
                    logger.info(f"{event_path} 蓝光，不处理")
                    return

                # 查询历史记录，已转移的不处理
                if self.transferhis.get_by_src(event_path):
                    logger.info(f"{event_path} 已整理过")
                    return

                # 查询转移目的目录
                target: Path = self._dirconf.get(mon_path)
                # 查询转移方式
                transfer_type = self._transferconf.get(mon_path)
                # # 根据父路径获取下载历史
                # download_history = self.downloadhis.get_by_path(Path(event_path).parent)

                file_meta, mediainfo = self.__recognize_media(file_path)
                if not file_meta:
                    logger.debug(f"{event_path} 不是jav文件")
                    return
                
                if not mediainfo:
                    logger.warn(f'未识别到媒体信息，番号：{file_meta.doubanid}')
                    # 新增转移成功历史记录
                    his = self.transferhis.add_fail(
                        src_path=file_path,
                        mode=transfer_type,
                        meta=file_meta
                    )
                    if self._notify:
                        self.chain.post_message(Notification(
                            mtype=NotificationType.Manual,
                            title=f"{file_path.name} 未识别到媒体信息，无法入库！"
                        ))
                    return
                
                logger.info(f"{file_path.name} 识别为：{mediainfo.type.value} {mediainfo.title_year}")

                episodes_info = None

                # 获取downloadhash
                download_hash = None # self.get_download_hash(src=str(file_path))

                # 转移
                transferinfo: TransferInfo = self.jav_file_transfer.transfer(mediainfo=mediainfo,
                                                                            path=file_path,
                                                                            transfer_type=transfer_type,
                                                                            target=target,
                                                                            rename_format=self._rename_format,
                                                                            meta=file_meta)

                if not transferinfo:
                    logger.error("文件转移模块运行失败")
                    return
                if not transferinfo.success:
                    # 转移失败
                    logger.warn(f"{file_path.name} 入库失败：{transferinfo.message}")
                    # 新增转移失败历史记录
                    self.transferhis.add_fail(
                        src_path=file_path,
                        mode=transfer_type,
                        download_hash=download_hash,
                        meta=file_meta,
                        mediainfo=mediainfo,
                        transferinfo=transferinfo
                    )
                    if self._notify:
                        self.chain.post_message(Notification(
                            mtype=NotificationType.Manual,
                            title=f"{mediainfo.title_year} 入库失败！",
                            text=f"原因：{transferinfo.message or '未知'}",
                            image=mediainfo.get_message_image()
                        ))
                    return

                # 新增转移成功历史记录
                self.transferhis.add_success(
                    src_path=file_path,
                    mode=transfer_type,
                    download_hash=download_hash,
                    meta=file_meta,
                    mediainfo=mediainfo,
                    transferinfo=transferinfo
                )

                # 刮削单个文件
                if self._scrap_metadata:
                    self.jav_scraper.scrape_metadata(path=transferinfo.target_path,
                                                    mediainfo=mediainfo,
                                                    transfer_type=transfer_type)

                # 发送消息汇总
                media_list = self._medias.get(mediainfo.title_year + " " + file_meta.season) or {}
                if media_list:
                    media_files = media_list.get("files") or []
                    if media_files:
                        file_exists = False
                        for file in media_files:
                            if str(event_path) == file.get("path"):
                                file_exists = True
                                break
                        if not file_exists:
                            media_files.append({
                                "path": event_path,
                                "mediainfo": mediainfo,
                                "file_meta": file_meta,
                                "transferinfo": transferinfo
                            })
                    else:
                        media_files = [
                            {
                                "path": event_path,
                                "mediainfo": mediainfo,
                                "file_meta": file_meta,
                                "transferinfo": transferinfo
                            }
                        ]
                    media_list = {
                        "files": media_files,
                        "time": datetime.datetime.now()
                    }
                else:
                    media_list = {
                        "files": [
                            {
                                "path": event_path,
                                "mediainfo": mediainfo,
                                "file_meta": file_meta,
                                "transferinfo": transferinfo
                            }
                        ],
                        "time": datetime.datetime.now()
                    }
                self._medias[mediainfo.title_year + " " + file_meta.season] = media_list

                # 广播事件
                self.eventmanager.send_event(EventType.TransferComplete, {
                    'meta': file_meta,
                    'mediainfo': mediainfo,
                    'transferinfo': transferinfo
                })

                # 移动模式删除空目录
                if transfer_type == "move":
                    for file_dir in file_path.parents:
                        if len(str(file_dir)) <= len(str(Path(mon_path))):
                            # 重要，删除到监控目录为止
                            break
                        files = SystemUtils.list_files(file_dir, settings.RMT_MEDIAEXT)
                        if not files:
                            logger.warn(f"移动模式，删除空目录：{file_dir}")
                            shutil.rmtree(file_dir, ignore_errors=True)

        except Exception as e:
            logger.error("目录监控发生错误：%s - %s" % (str(e), traceback.format_exc()))

    def send_msg(self):
        """
        定时检查是否有媒体处理完，发送统一消息
        """
        if not self._medias or not self._medias.keys():
            return

        # 遍历检查是否已刮削完，发送消息
        for medis_title_year_season in list(self._medias.keys()):
            media_list = self._medias.get(medis_title_year_season)
            logger.info(f"开始处理媒体 {medis_title_year_season} 消息")

            if not media_list:
                continue

            # 获取最后更新时间
            last_update_time = media_list.get("time")
            media_files = media_list.get("files")
            if not last_update_time or not media_files:
                continue

            transferinfo = media_files[0].get("transferinfo")
            file_meta = media_files[0].get("file_meta")
            mediainfo = media_files[0].get("mediainfo")
            # 判断剧集最后更新时间距现在是已超过10秒或者电影，发送消息
            if (datetime.datetime.now() - last_update_time).total_seconds() > int(self._interval) \
                    or mediainfo.type == MediaType.MOVIE:
                # 发送通知
                if self._notify:

                    # 汇总处理文件总大小
                    total_size = 0
                    file_count = 0

                    # 剧集汇总
                    episodes = []
                    for file in media_files:
                        transferinfo = file.get("transferinfo")
                        total_size += transferinfo.total_size
                        file_count += 1

                        file_meta = file.get("file_meta")
                        if file_meta and file_meta.begin_episode:
                            episodes.append(file_meta.begin_episode)

                    transferinfo.total_size = total_size
                    # 汇总处理文件数量
                    transferinfo.file_count = file_count

                    # 剧集季集信息 S01 E01-E04 || S01 E01、E02、E04
                    season_episode = None
                    # 处理文件多，说明是剧集，显示季入库消息
                    if mediainfo.type == MediaType.TV:
                        # 季集文本
                        season_episode = f"{file_meta.season} {StringUtils.format_ep(episodes)}"
                    # 发送消息
                    self.transferchian.send_transfer_message(meta=file_meta,
                                                             mediainfo=mediainfo,
                                                             transferinfo=transferinfo,
                                                             season_episode=season_episode)
                # 发送完消息，移出key
                del self._medias[medis_title_year_season]
                continue

    def get_download_hash(self, src: str):
        """
        从表中获取download_hash，避免连接下载器
        """
        download_file = self.downloadhis.get_file_by_fullpath(src)
        if download_file:
            return download_file.download_hash
        return None

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        :return: 命令关键字、事件、描述、附带数据
        """
        return [{
            "cmd": "/directory_sync",
            "event": EventType.PluginAction,
            "desc": "目录监控同步",
            "category": "管理",
            "data": {
                "action": "directory_sync"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/directory_sync",
            "endpoint": self.sync,
            "methods": ["GET"],
            "summary": "目录监控同步",
            "description": "目录监控同步",
        }]

    def sync(self) -> schemas.Response:
        """
        API调用目录同步
        """
        self.sync_all()
        return schemas.Response(success=True)

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'scrap_metadata',
                                            'label': '刮削数据',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'mode',
                                            'label': '监控模式',
                                            'items': [
                                                {'title': '兼容模式', 'value': 'compatibility'},
                                                {'title': '性能模式', 'value': 'fast'}
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'transfer_type',
                                            'label': '转移方式',
                                            'items': [
                                                {'title': '移动', 'value': 'move'},
                                                {'title': '复制', 'value': 'copy'},
                                                {'title': '硬链接', 'value': 'link'},
                                                {'title': '软链接', 'value': 'softlink'},
                                                {'title': 'Rclone复制', 'value': 'rclone_copy'},
                                                {'title': 'Rclone移动', 'value': 'rclone_move'}
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'interval',
                                            'label': '入库消息延迟',
                                            'placeholder': '10'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '定时全量同步周期',
                                            'placeholder': '5位cron表达式，留空关闭'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'monitor_dirs',
                                            'label': '监控目录',
                                            'rows': 5,
                                            'placeholder': '每一行一个目录，支持以下几种配置方式，转移方式支持 move、copy、link、softlink、rclone_copy、rclone_move：\n'
                                                           '监控目录\n'
                                                           '监控目录#转移方式\n'
                                                           '监控目录:转移目的目录\n'
                                                           '监控目录:转移目的目录#转移方式'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'rename_format',
                                            'label': '重命名格式',
                                            'rows': 1,
                                            'placeholder': 'e.g.' + self.DEFAULT_RENAME_FORMAT
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'exclude_keywords',
                                            'label': '排除关键词',
                                            'rows': 2,
                                            'placeholder': '每一行一个关键词'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'onlyonce_path',
                                            'label': '指定路径（仅本次运行）',
                                            'rows': 1,
                                            'placeholder': '指定路径'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '入库消息延迟默认10s，如网络较慢可酌情调大，有助于发送统一入库消息。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": False,
            "onlyonce": False,
            "mode": "fast",
            "transfer_type": settings.TRANSFER_TYPE,
            "monitor_dirs": "",
            "exclude_keywords": "",
            "interval": 10,
            "cron": "",
            "scrap_metadata": False,
            "rename_format": self.DEFAULT_RENAME_FORMAT,
            "onlyonce_path": "",
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        if self._observer:
            for observer in self._observer:
                try:
                    observer.stop()
                    observer.join()
                except Exception as e:
                    print(str(e))
        self._observer = []
        if self._scheduler:
            self._scheduler.remove_all_jobs()
            if self._scheduler.running:
                self._event.set()
                self._scheduler.shutdown()
                self._event.clear()
            self._scheduler = None

    def is_jav(self, title):
        if not title:
            return None
        if title.endswith('/'):
            title = title[:-1]
        else:
            title = title[title.rfind('/')+1:]
        title = title.upper().replace("SIS001", "").replace("1080P", "").replace("720P", "").replace("2160P", "")
        t = re.search(r'T28[\-_]\d{3,4}', title)
        # 一本道
        if not t:
            t = re.search(r'1PONDO[\-_]\d{6}[\-_]\d{2,4}', title)
            if t:
                t = t.group().replace("1PONDO_", "").replace("1PONDO-", "")
        if not t:
            t = re.search(r'HEYZO[\-_]?\d{4}', title)
        if not t:
            # 加勒比
            t = re.search(r'CARIB[\-_]\d{6}[\-_]\d{3}' ,title)
            if t:
                t = t.group().replace("CARIB-", "").replace("CARIB_", "")
        if not t:
            # 东京热
            t = re.search(r'N[-_]\d{4}' ,title)
        
        if not t:
            # Jukujo-Club | 熟女俱乐部
            t = re.search(r'JUKUJO[-_]\d{4}' ,title)

        # 通用
        # if not t:
        #     t = re.search(r'S[A-Z]{1,4}[-_]\d{3,5}' ,title)
        if not t:
            t = re.search(r'[A-Z]{2,5}[-_]\d{3,5}' ,title)
        if not t:
            t = re.search(r'\d{6}[\-_]\d{2,4}' ,title)

        # if not t:
        #     t = re.search(r'[A-Z]+\d{3,5}' ,title)
        
        # if not t:
        #     t = re.search(r'[A-Za-z]+[-_]?\d+' ,title)
        
        # if not t:
        #     t = re.search(r'\d+[-_]?\d+' ,title)
            
        if not t:
            return None
        else:
            t = t.group().replace("_", "-")
            return t
        
    def is_jav_chinese(self, title):
        return '字幕' in title or '-c' in title or '-C' in title

    def JavMetaInfo(self, title: str, subtitle: str = None) -> MetaBase:
        """
        根据标题和副标题识别元数据
        :param title: 标题、种子名、文件名
        :param subtitle: 副标题、描述
        :return: MetaAnime、MetaVideo
        """
        # 原标题
        org_title = title
        if title and Path(title).suffix.lower() in settings.RMT_MEDIAEXT:
            isfile = True
        else:
            isfile = False
        # 番号
        title = self.is_jav(title)
        # 副标题为番号
        meta = MetaVideo(title, title, isfile)# 记录原标题
        # 原始文件名
        meta.title = org_title
        meta.apply_words = []
        meta.type = JavMediaType.JAV
        meta.cn_subtitle = self.is_jav_chinese(org_title)
        # doubanid为番号
        meta.doubanid = title
        return meta

        # 原标题
        org_title = title
        # 预处理标题
        title, apply_words = code ,code
        # 获取标题中媒体信息
        title, metainfo = find_metainfo(title)
        # 判断是否处理文件
        if title and Path(title).suffix.lower() in settings.RMT_MEDIAEXT:
            isfile = True
        else:
            isfile = False
        # 识别
        meta = MetaAnime(title, subtitle, isfile) if is_anime(title) else MetaVideo(title, subtitle, isfile)
        # 记录原标题
        meta.title = org_title
        #  记录使用的识别词
        meta.apply_words = apply_words or []
        # 修正媒体信息
        if metainfo.get('tmdbid'):
            meta.tmdbid = metainfo['tmdbid']
        if metainfo.get('doubanid'):
            meta.tmdbid = metainfo['doubanid']
        if metainfo.get('type'):
            meta.type = metainfo['type']
        if metainfo.get('begin_season'):
            meta.begin_season = metainfo['begin_season']
        if metainfo.get('end_season'):
            meta.end_season = metainfo['end_season']
        if metainfo.get('total_season'):
            meta.total_season = metainfo['total_season']
        if metainfo.get('begin_episode'):
            meta.begin_episode = metainfo['begin_episode']
        if metainfo.get('end_episode'):
            meta.end_episode = metainfo['end_episode']
        if metainfo.get('total_episode'):
            meta.total_episode = metainfo['total_episode']
        return meta


    def JavMetaInfoPath(self, path: Path) -> MetaBase:
        """
        根据路径识别元数据
        :param path: 路径
        """
        # 上级目录元数据
        dir_meta = self.JavMetaInfo(title=path.parent.name)
        # 文件元数据，不包含后缀
        file_meta = self.JavMetaInfo(title=path.stem)
        # 合并元数据
        file_meta.merge(dir_meta)
        return file_meta