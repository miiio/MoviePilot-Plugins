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
import time

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
from .javbus import JavbusWeb

class JavSubscribe(_PluginBase):
    # 插件名称
    plugin_name = "Jav订阅"
    # 插件描述
    plugin_desc = "监控指定Jav页面，自动离线到115并通过aria2下载到本地"
    # 插件图标
    plugin_icon = "movie.jpg"
    # 插件版本
    plugin_version = "0.8.6"
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
    _auto_download = False
    _clear = False
    _clearflag = False
    _aria2_host = "http://192.168.1.10"
    _aria2_port = 6802
    _aria2_secret = "3515"
    _searching = False
    _115_max_downloading_num = 5

    def init_plugin(self, config: dict = None):
        self.media_server_db = get_db().__next__()
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
            self._auto_download = config.get("_auto_download")
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

    def _get_current_downloading_count(self):
        count = 0
        for item in self.aria2.get_downloads():
            if not item.is_active: continue
            def _check(item: aria2p.Download):
                for file in item.files:
                    for uri in file.uris:
                        if uri['uri'] and "115.com" in uri['uri']:
                            return True
                return False
            count += 1 if _check(item) else 0
        return count
    
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
        
        # 清理已存在的数据
        exits_jav_list = [item['id'] for item in wait_download_queue if self.jav_exists_by_javid(item['id'])]
        logger.info(f"清理媒体库中已存在数据：" + ",".join(exits_jav_list)")
        wait_download_queue = [item for item in wait_download_queue if item['id'] not in exits_jav_list]

        for addr in addr_list:
            if not addr:
                continue
            try:
                logger.info(f"获取订阅地址：{addr} ...")
                addrs_info = self.__get_addrs_info(addr, history, wait_download_queue)
                if not addrs_info or len(addrs_info) == 0:
                    logger.info(f"订阅地址：{addr} ，未查询到数据")
                    continue
                
                logger.info(f"订阅地址：{addr} ，共 {len(addrs_info)} 条数据")
                wait_download_queue.extend(addrs_info)
            except Exception as e:
                logger.error(str(e))

        # 去重
        unique_dict = {item['id']: item for item in wait_download_queue}
        # 获取去重后的列表
        wait_download_queue = list(unique_dict.values())
        
        logger.info(f"当前待处理共 {len(wait_download_queue)} 条数据")

        if self._auto_download:
            self.jav115 = Jav115()
            logger.info(f"开始处理待下载任务...")
            for item in wait_download_queue[:]:
                count = self._get_current_downloading_count()
                if count >= self._115_max_downloading_num:
                    logger.info(f"当前下载任务数：{count}，结束订阅刷新任务.")
                    break
                    
                javid = item['id']
                if not javid or not self.is_jav(javid): continue
                # 处理每一个番号
                if self._vote > 0:
                    javlib_info = self.javlibWeb.detail_by_javid(item['id'])
                    if not javlib_info or javlib_info.get('rating', -1) < self._vote:
                        wait_download_queue.remove(item)
                        history.append(item)
                        logger.info(item['id'] + ' javlib评分：' + javlib_info.get('rating', 0) + " 不满足条件")
                        continue
                
                # 搜索资源
                download_info = None
                try:
                    time.sleep(3)
                    download_info = self.jav115.search_and_download_jav_115(item['id'])
                    if download_info and download_info.url and download_info.headers:
                        logger.info(f"{item['id']} 115离线下载成功，开始下载到本地...")
                        # 提交下载 aria2
                        download_headers = '\r\n'.join([f'{key}: {value}' for key, value in download_info.headers.items()])
                        aria2_download_info = self.aria2.add_uris([download_info.url], options={'header': download_headers})
                        if not aria2_download_info or not aria2_download_info.name:
                            logger.error("[aria2] aria2下载任务提交异常")
                        else:
                            logger.info(f"{item['id']} 下载任务创建成功: {aria2_download_info.name}")
                        wait_download_queue.remove(item)
                        history.append(item)
                        logger.info(f"{item['id']} 处理完成")
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
    
    def __get_addrs_info(self, addr, history, wait_download_queue) -> List[dict]:
        if not addr: return []
        logger.info(f"获取页面数据：{addr} ...")
        info_list = []
        if "javmenu.com" in addr:
            info_list = JavMenuWeb().page_jav_list(addr)['jav_list']
        elif "javbus" in addr:
            info_list = JavbusWeb().page_jav_list(addr)['jav_list']

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
            addrs_infos.append(info)
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
            "clear": self._clear,
            "auto_download": self._auto_download,
        })

    
    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        """
        # 查询历史记录
        historys = self.get_data('history')
        wait_download_queue = self.get_data('wait_download_queue')
        if not historys and not wait_download_queue:
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]
        # 拼装页面
        contents = []
        for list_item in [wait_download_queue, historys]:
            content = []
            for item in list_item:
                title = item.get("title") if len(item.get("title")) <= 8 else item.get("title")[:8] + "..."
                poster = item.get("img")
                id = item.get("id")
                date = item.get("date")
                content.append(
                {
                    'component': 'VCard',
                    'content': [
                        {
                            'component': 'div',
                            'props': {
                                'class': 'd-flex justify-space-start flex-nowrap flex-row',
                            },
                            'content': [
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VImg',
                                            'props': {
                                                'src': poster,
                                                'height': 73,
                                                'width': 111,
                                                'aspect-ratio': '11/7',
                                                'class': 'object-cover shadow ring-gray-500',
                                                'cover': True
                                            }
                                        }
                                    ]
                                },
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VCardSubtitle',
                                            'props': {
                                                'class': 'pa-2 font-bold break-words whitespace-break-spaces'
                                            },
                                            'content': [
                                                {
                                                    'component': 'a',
                                                    'props': {
                                                        'href': f"https://javmenu.com/zh/{id}",
                                                        'target': '_blank'
                                                    },
                                                    'text': title
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'番号：{id}'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'发行时间：{date}'
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            )
            contents.append(content)
        
        
        # return [
        #     {
        #         'component': 'VCard',
        #         'content': [
        #             {
        #                 'component': 'VTabs',
        #                 'props': {'v-model': 'tab'},
        #                 'content': [
        #                     {
        #                         'component': 'VTab',
        #                         'props': {'value': 'wait_queue'},
        #                         'content': '待刷新'
        #                     },
        #                     {
        #                         'component': 'VTab',
        #                         'props': {'value': 'history'},
        #                         'content': '已完成'
        #                     }
        #                 ]
        #             },
        #             {
        #                 'component': 'VWindow',
        #                 'props': {'v-model': 'tab'},
        #                 'content':[
        #                     {
        #                         'component': 'VWindowItem',
        #                         'props': {'value': 'wait_queue'},
        #                         'content': contents[0]
        #                     },
        #                     {
        #                         'component': 'VWindowItem',
        #                         'props': {'value': 'history'},
        #                         'content': contents[1]
        #                     }
        #                 ]
        #             }
        #         ]
        #     },
        # ]

        return [
            {
                'component': 'div',
                'props': {
                    'class': 'grid gap-3 grid-info-card',
                },
                'content': contents[0]
            }
        ]


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
                                            'label': '预设页面',
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
    
    def jav_exists_by_javid(self, javid: str):
        return self.media_server_db.query(MediaServerItem).filter(MediaServerItem.title.like(f"%{javid}%")).first()
    
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