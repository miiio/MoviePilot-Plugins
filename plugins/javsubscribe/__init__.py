import datetime
import re
import xml.dom.minidom
from threading import Event
from typing import Tuple, List, Dict, Any

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import aria2p
from app.db import get_db

from app.chain.download import DownloadChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.utils.dom import DomUtils
from app.utils.http import RequestUtils
from app.db import db_query
from app.db.models.mediaserver import MediaServerItem
from app.db.models.site import Site

from .javmenu import JavMenuWeb
from .javlib import JavlibWeb
from .jav115 import Jav115

class JavSubscribe(_PluginBase):
    # 插件名称
    plugin_name = "Jav订阅"
    # 插件描述
    plugin_desc = "监控指定Jav页面，自动通过M-Team搜索下载"
    # 插件图标
    plugin_icon = "movie.jpg"
    # 插件版本
    plugin_version = "0.3.7"
    # 插件作者
    plugin_author = "boji"
    # 作者主页
    author_url = "https://github.com"
    # 插件配置项ID前缀
    plugin_config_prefix = "jav_subscribe_"
    # 加载顺序
    plugin_order = 6
    # 可使用的用户级别
    auth_level = 2

    # 退出事件
    _event = Event()
    # 私有属性
    downloadchain: DownloadChain = None
    mteam_info: Site = None
    _scheduler = None

    _enabled = False
    _cron = ""
    _onlyonce = False
    _custom_addrs = []
    _ranks = []
    _vote = 0
    _clear = False
    _clearflag = False
    _aria2_host = "http://192.168.1.10"
    _aria2_port = 6802
    _aria2_secret = "3515"
    _searching = False

    def init_plugin(self, config: dict = None):
        self.jav115 = None
        self.downloadchain = DownloadChain()
        self.javlibWeb = JavlibWeb()
        self.aria2 = aria2p.API(
            aria2p.Client(
                host=self._aria2_host,
                port=self._aria2_port,
                secret=self._aria2_secret
            )
        )
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce")
            self._vote = float(config.get("vote")) if config.get("vote") else 0
            custom_addrs = config.get("custom_addrs")
            if custom_addrs:
                if isinstance(custom_addrs, str):
                    self._custom_addrs = custom_addrs.split('\n')
                else:
                    self._custom_addrs = custom_addrs
            else:
                self._custom_addrs = []
            self._ranks = config.get("ranks") or []
            self._clear = config.get("clear")

        # 停止现有任务
        self.stop_service()

        # 启动服务
        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            if self._cron:
                logger.info(f"Jav订阅服务启动，周期：{self._cron}")
                
                try:
                    self._scheduler.add_job(func=self.__refresh_subscribe,
                                            trigger=CronTrigger.from_crontab(self._cron),
                                            name="Jav订阅")
                except Exception as e:
                    logger.error(f"Jav订阅服务启动失败，错误信息：{str(e)}")
                    self.systemmessage.put(f"Jav订阅服务启动失败，错误信息：{str(e)}")

            if self._onlyonce:
                logger.info("Jav订阅服务启动，立即运行一次")
                self._scheduler.add_job(func=self.__refresh_subscribe, trigger='date',
                                        run_date=datetime.datetime.now(
                                            tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                        )

            if self._onlyonce or self._clear:
                # 关闭一次性开关
                self._onlyonce = False
                # 记录缓存清理标志
                self._clearflag = self._clear
                # 关闭清理缓存
                self._clear = False
                # 保存配置
                self.__update_config()

            if self._scheduler.get_jobs():
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    def __refresh_subscribe(self):
        if self._searching:
            logger.warn(f"当前有任务尚未结束，取消刷新...")

        self._searching = True
        logger.info(f"开始刷新Jav订阅数据 ...")
        
        addr_list = self._ranks + self._custom_addrs
        if not addr_list:
            logger.info(f"未设置订阅地址")
            return
        else:
            logger.info(f"共 {len(addr_list)} 个订阅地址需要刷新")
        
        # 读取历史记录
        if self._clearflag:
            wait_download_queue = []
            history = []
        else:
            wait_download_queue: List[dict] = self.get_data('wait_download_queue') or []
            history: List[dict] = self.get_data('history') or []
        
        for addr in addr_list:
            if not addr:
                continue
            try:
                logger.info(f"获取订阅地址：{addr} ...")
                addrs_info = self.__get_addrs_info(addr)
                if not addrs_info or len(addrs_info):
                    logger.info(f"订阅地址：{addr} ，未查询到数据")
                    continue
                
                logger.info(f"订阅地址：{addr} ，共 {len(addrs_info)} 条数据")
                wait_download_queue.extend(addrs_info)
            except Exception as e:
                logger.error(str(e))

        self.jav115 = Jav115()
        logger.info(f"开始处理待下载任务...")
        for item in wait_download_queue[:]:
            javid = item['id']
            if not javid or not self.is_jav(javid): continue
            # 处理每一个番号
            if self._vote > 0:
                javlib_info = self.javlibWeb.detail_by_javid(item['id'])
                if javlib_info.get('rating', -1) < self._vote:
                    wait_download_queue.remove(item)
                    history.append(item)
                    logger.info(item['id'] + ' javlib评分：' + javlib_info.get('rating', 0) + " 不满足条件")
                    continue
            
            # 搜索资源
            download_info = None
            try:
                download_info = self.jav115.search_and_download_jav_115(item['id'])
                if download_info and download_info.url and download_info.headers:
                    logger.info(f"{item['id']} 115离线下载成功，开始下载到本地...")
                    # 提交下载 aria2
                    download_headers = download_info.headers
                    aria2_download_info = self.aria2.add_uris([download_info.url], options={'header': download_headers})
                    if not aria2_download_info or not aria2_download_info.name:
                        logger.error("[aria2] aria2下载任务提交异常")
                    else:
                        logger.info(f"{item['id']} 下载任务创建成功: {aria2_download_info.name}")
                    wait_download_queue.remove(item)
                    history.append(item)
                else:
                    logger.info(f"{item['id']} 搜索&下载失败")
            except e:
                logger.error(str(e))
                logger.info(f"{item['id']} 搜索&下载失败")


        # 保存历史记录
        self.save_data('wait_download_queue', wait_download_queue)
        self.save_data('history', history)
        # 缓存只清理一次
        self._clearflag = False
        logger.info(f"所有订阅地址刷新完成")
        self._searching = False
    
    def __get_addrs_info(self, addr) -> List[dict]:
        if not addr: return []
        logger.info(f"获取页面数据：{addr} ...")
        info_list = []
        if "javmenu.com" in addr:
            info_list = JavMenuWeb().page_jav_list(addr)['jav_list']

        # 读取历史记录
        if self._clearflag:
            wait_download_queue = []
            history = []
        else:
            wait_download_queue: List[dict] = self.get_data('wait_download_queue') or []
            history: List[dict] = self.get_data('history') or []

        addrs_infos = []
        # 过滤已处理过的
        for info in info_list:
            if info and not self.is_jav(info['id']):
                continue
            info['id'] = self.is_jav(info['id'])
            if info and info['id'] in [h.get("id") for h in wait_download_queue]:
                continue
            if info and info['id'] in [h.get("id") for h in history]:
                continue
            itemid = self.jav_exists_by_javid(info.get('id'))
            if itemid is not None:
                logger.info(info['id'] + ' 媒体库中已存在')
                continue

        logger.info(f"页面地址：{addr} ，共 {len(addrs_infos)} 条数据")
        return addrs_infos

    def stop_service(self):
        """
        停止服务
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            print(str(e))

    def get_state(self) -> bool:
        return self._enabled
    
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def __update_config(self):
        """
        列新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "vote": self._vote,
            "ranks": self._ranks,
            "custom_addrs": '\n'.join(map(str, self._custom_addrs)),
            "clear": self._clear
        })

    def get_page(self) -> List[dict]:
        pass

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
                                    'md': 8
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
                                    'md': 8
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
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '5位cron表达式，留空自动'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'vote',
                                            'label': '评分',
                                            'placeholder': 'JavLib评分大于等于该值才订阅'
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
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'model': 'ranks',
                                            'label': '预设榜单',
                                            'items': [
                                                {'title': 'JavMenu日榜', 'value': 'https://javmenu.com/zh/rank/censored/day'},
                                                {'title': 'JavMenu周榜', 'value': 'https://javmenu.com/zh/rank/censored/week'},
                                                {'title': 'JavMenu月榜', 'value': 'https://javmenu.com/zh/rank/censored/month'},
                                            ]
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
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'custom_addrs',
                                            'label': '自定义订阅页面地址',
                                            'placeholder': '每行一个地址，如：https://www.javbus.com/star/pmv'
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
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'clear',
                                            'label': '清理历史记录',
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
            "cron": "",
            "onlyonce": False,
            "vote": "",
            "ranks": [],
            "custom_addrs": "",
            "clear": False
        }
    
    def jav_exists_by_javid(self, javid: str, db: Session = Depends(get_db)):
        return self._jav_exists_by_javid(db, javid)

    @staticmethod
    @db_query
    def _jav_exists_by_javid(db: Session, javid: str):
        return db.query(MediaServerItem).filter(MediaServerItem.title.like(javid)).first()
    
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