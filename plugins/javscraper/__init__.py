import re
import traceback
from datetime import datetime, timedelta
from multiprocessing.dummy import Pool as ThreadPool
from multiprocessing.pool import ThreadPool
from typing import Any, List, Dict, Tuple, Optional
from urllib.parse import urljoin
import mysql.connector
from datetime import datetime

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import EventManager, eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.utils.timer import TimerUtils

from .javranking import JavRanking

from .javmenu import JavMenuWeb

class Javscraper(_PluginBase):
    # 插件名称
    plugin_name = "Jav数据抓取器"
    # 插件描述
    plugin_desc = "自动抓取Jav数据"
    # 插件图标
    plugin_icon = "statistic.png"
    # 插件版本
    plugin_version = "0.0.2"
    # 插件作者
    plugin_author = "miiio"
    # 作者主页
    author_url = "https://github.com/miiio"
    # 插件配置项ID前缀
    plugin_config_prefix = "jav_scraper_"
    # 加载顺序
    plugin_order = 0
    # 可使用的用户级别
    auth_level = 2

    rank_list_javmenu_censored_day = "javmenu:censored:day"

    # 事件管理器
    event: EventManager = None
    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None
    # Mysql连接
    _cnx = None

    # 配置属性
    _enabled: bool = False
    _cron: str = ""
    _onlyonce: bool = False
    _notify: bool = False
    _mysql_host: str = ""
    _mysql_port: str = ""
    _mysql_username: str = ""
    _mysql_password: str = ""
    _rank_list: list = []
    _ignore_list = None
    _start_time: int = None
    _end_time: int = None

    def init_plugin(self, config: dict = None):
        self.event = EventManager()
        self.javmenu = JavMenuWeb()

        # 停止现有任务
        self.stop_service()

        # 配置
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce")
            self._notify = config.get("notify")
            self._mysql_host = config.get("mysql_host")
            self._mysql_port = config.get("mysql_port")
            self._mysql_username = config.get("mysql_username")
            self._mysql_password = config.get("mysql_password")
            self._rank_list = config.get("rank_list") or []
            self._ignore_list = config.get("ignore_list")

            # 保存配置
            self.__update_config()

        # 加载模块
        if self._enabled or self._onlyonce:

            # 立即运行一次
            if self._onlyonce:
                # 定时服务
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                logger.info("Jav数据抓取服务启动，立即运行一次")
                self._scheduler.add_job(func=self.scraper, trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                        name="Jav数据抓取")

                # 关闭一次性开关
                self._onlyonce = False
                # 保存配置
                self.__update_config()

                # 启动任务
                if self._scheduler.get_jobs():
                    self._scheduler.print_jobs()
                    self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    def __update_config(self):
        # 保存配置
        self.update_config(
            {
                "enabled": self._enabled,
                "notify": self._notify,
                "cron": self._cron,
                "onlyonce": self._onlyonce,
                "mysql_host": self._mysql_host,
                "mysql_port": self._mysql_port,
                "mysql_username": self._mysql_username,
                "mysql_password": self._mysql_password,
                "rank_list": self._rank_list,
                "ignore_list": self._ignore_list,
            }
        )

    # @staticmethod
    # def get_command() -> List[Dict[str, Any]]:
    #     """
    #     定义远程控制命令
    #     :return: 命令关键字、事件、描述、附带数据
    #     """
    #     return [{
    #         "cmd": "/site_signin",
    #         "event": EventType.PluginAction,
    #         "desc": "站点签到",
    #         "category": "站点",
    #         "data": {
    #             "action": "site_signin"
    #         }
    #     }]

    # def get_api(self) -> List[Dict[str, Any]]:
    #     """
    #     获取插件API
    #     [{
    #         "path": "/xx",
    #         "endpoint": self.xxx,
    #         "methods": ["GET", "POST"],
    #         "summary": "API说明"
    #     }]
    #     """
    #     return [{
    #         "path": "/signin_by_domain",
    #         "endpoint": self.signin_by_domain,
    #         "methods": ["GET"],
    #         "summary": "站点签到",
    #         "description": "使用站点域名签到站点",
    #     }]

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._cron:
            try:
                if str(self._cron).strip().count(" ") == 4:
                    return [{
                        "id": "JavScraper",
                        "name": "Jav数据抓取",
                        "trigger": CronTrigger.from_crontab(self._cron),
                        "func": self.scraper,
                        "kwargs": {}
                    }]
                else:
                    # 2.3/9-23
                    crons = str(self._cron).strip().split("/")
                    if len(crons) == 2:
                        # 2.3
                        cron = crons[0]
                        # 9-23
                        times = crons[1].split("-")
                        if len(times) == 2:
                            # 9
                            self._start_time = int(times[0])
                            # 23
                            self._end_time = int(times[1])
                        if self._start_time and self._end_time:
                            return [{
                                "id": "JavScraper",
                                "name": "Jav数据抓取",
                                "trigger": "interval",
                                "func": self.scraper,
                                "kwargs": {
                                    "hours": float(str(cron).strip()),
                                }
                            }]
                        else:
                            logger.error("Jav数据抓取服务启动失败，周期格式错误")
                    else:
                        # 默认0-24 按照周期运行
                        return [{
                            "id": "JavScraper",
                            "name": "Jav数据抓取",
                            "trigger": "interval",
                            "func": self.scraper,
                            "kwargs": {
                                "hours": float(str(self._cron).strip()),
                            }
                        }]
            except Exception as err:
                logger.error(f"定时任务配置错误：{str(err)}")
        elif self._enabled:
            # 随机时间
            triggers = TimerUtils.random_scheduler(num_executions=1,
                                                   begin_hour=20,
                                                   end_hour=23,
                                                   max_interval=6 * 60,
                                                   min_interval=2 * 60)
            ret_jobs = []
            for trigger in triggers:
                ret_jobs.append({
                    "id": f"JavScraper|{trigger.hour}:{trigger.minute}",
                    "name": "Jav数据抓取",
                    "trigger": "cron",
                    "func": self.scraper,
                    "kwargs": {
                        "hour": trigger.hour,
                        "minute": trigger.minute
                    }
                })
            return ret_jobs
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 站点的可选项（内置站点 + 自定义站点）
        customSites = self.__custom_sites()

        rank_options = ([
            {
                "title": "JavMenu有码日榜",
                "value": self.rank_list_javmenu_censored_day
            }
        ])
        
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
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            },
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
                                            'model': 'ignore_list',
                                            'label': '屏蔽词',
                                            'placeholder': '屏蔽词'
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
                                            'model': 'mysql_host',
                                            'label': 'MysqlHost',
                                            'placeholder': '192.168.1.10'
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
                                            'model': 'mysql_port',
                                            'label': 'MysqlPort',
                                            'placeholder': '3306'
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
                                            'model': 'mysql_username',
                                            'label': 'MysqlUserName',
                                            'placeholder': 'root'
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
                                            'model': 'mysql_password',
                                            'label': 'MysqlPassword',
                                            'placeholder': ''
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
                                            'model': 'rank_list',
                                            'label': '签到站点',
                                            'items': rank_options
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    
                    # {
                    #     'component': 'VRow',
                    #     'content': [
                    #         {
                    #             'component': 'VCol',
                    #             'props': {
                    #                 'cols': 12,
                    #             },
                    #             'content': [
                    #                 {
                    #                     'component': 'VAlert',
                    #                     'props': {
                    #                         'type': 'info',
                    #                         'variant': 'tonal',
                    #                         'text': '执行周期支持：'
                    #                                 '1、5位cron表达式；'
                    #                                 '2、配置间隔（小时），如2.3/9-23（9-23点之间每隔2.3小时执行一次）；'
                    #                                 '3、周期不填默认9-23点随机执行2次。'
                    #                                 '每天首次全量执行，其余执行命中重试关键词的站点。'
                    #                     }
                    #                 }
                    #             ]
                    #         }
                    #     ]
                    # },
                    # {
                    #     'component': 'VRow',
                    #     'content': [
                    #         {
                    #             'component': 'VCol',
                    #             'props': {
                    #                 'cols': 12,
                    #             },
                    #             'content': [
                    #                 {
                    #                     'component': 'VAlert',
                    #                     'props': {
                    #                         'type': 'info',
                    #                         'variant': 'tonal',
                    #                         'text': '自动优选：0-关闭，命中重试关键词次数大于该数量时自动执行Cloudflare IP优选（需要开启且则正确配置Cloudflare IP优选插件和自定义Hosts插件）'
                    #                     }
                    #                 }
                    #             ]
                    #         }
                    #     ]
                    # }
                ]
            }
        ], {
            "enabled": False,
            "notify": False,
            "cron": "",
            "onlyonce": False,
            "rank_list": [self.rank_list_javmenu_censored_day],
            "ignore_list": "",
            "mysql_host": "",
            "mysql_port": "",
            "mysql_username": "",
            "mysql_password": "",
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        """
        scrape_data = self.get_data("scrape_history")
        if scrape_data:
            contents = [
                {
                    'component': 'tr',
                    'props': {
                        'class': 'text-sm'
                    },
                    'content': [
                        {
                            'component': 'td',
                            'props': {
                                'class': 'whitespace-nowrap break-keep text-high-emphasis'
                            },
                            'text': data.get("time")
                        },
                        {
                            'component': 'td',
                            'text': data.get("source")
                        },
                        {
                            'component': 'td',
                            'text': data.get("info")
                        }
                    ]
                } for data in scrape_data
            ]
        else:
            contents = [
                {
                    'component': 'tr',
                    'props': {
                        'class': 'text-sm'
                    },
                    'content': [
                        {
                            'component': 'td',
                            'props': {
                                'colspan': 3,
                                'class': 'text-center'
                            },
                            'text': '暂无数据'
                        }
                    ]
                }
            ]
        return [
            {
                'component': 'VTable',
                'props': {
                    'hover': True
                },
                'content': [
                    {
                        'component': 'thead',
                        'content': [
                            {
                                'component': 'th',
                                'props': {
                                    'class': 'text-start ps-4'
                                },
                                'text': '抓取时间'
                            },
                            {
                                'component': 'th',
                                'props': {
                                    'class': 'text-start ps-4'
                                },
                                'text': '抓取类型'
                            },
                            {
                                'component': 'th',
                                'props': {
                                    'class': 'text-start ps-4'
                                },
                                'text': '抓取记录'
                            }
                        ]
                    },
                    {
                        'component': 'tbody',
                        'content': contents
                    }
                ]
            }
        ]

    def scraper(self, event: Event = None):
        """
        开始抓取jav数据
        """

        # 日期
        today = datetime.today()
        if self._start_time and self._end_time:
            if int(datetime.today().hour) < self._start_time or int(datetime.today().hour) > self._end_time:
                logger.error(
                    f"当前时间 {int(datetime.today().hour)} 不在 {self._start_time}-{self._end_time} 范围内，暂不执行任务")
                return
        logger.info("[JavScraper]开始连接mysql数据库 ...")
        
        try:
            self._cnx = mysql.connector.connect(
                host=self._mysql_host,
                port=self._mysql_port,
                user=self._mysql_username,
                password=self._mysql_password,
                database="jav"
            )
        except Exception as e:
            self._cnx = None
            logger.error("[JavScraper]连接mysql数据库失败 ...")
            return

        logger.info("[JavScraper]开始执行Jav数据抓取 ...")

        if self._rank_list:
            self.__do_scrape_rank_list(rank_list=self._rank_list)

        
        logger.info("[JavScraper]断开mysql数据库 ...")
        if self._cnx:
            self._cnx.close()


    def __do_scrape_rank_list(self, rank_list):
        for rank in rank_list:
            if rank == self.rank_list_javmenu_censored_day:
                self.__do_scrape_javmenu_rank(rank_type="censored", period="day")

        pass

    def __do_scrape_javmenu_rank(self, rank_type: str, period: str):
        logger.info("[JavScraper][javmenu]开始抓取JavMenu有码日榜 ...")

        ret = self.javmenu.rank_list(type=rank_type, rank_type=period, page=1)

        javlist = ret['jav_list']
        logger.info("[JavScraper][javmenu]JavMenu有码日榜抓取完成，共 %s 条数据" % len(javlist))
        logger.info("[JavScraper][javmenu]开始写入数据库 ...")
        for ind, jav in enumerate(javlist):
            javranking = JavRanking(av_number=jav.get("id", ""), av_title=jav.get("title", ""), av_cover=jav.get("img", ""), release_date=self.__format_date(jav.get("title", None)),
                                    is_downloadable=jav.get('is_downloadable', False), has_subtitle=jav.get('has_subtitle', False), ranking=jav.get('rank', 0), 
                                    has_code=rank_type=="censored", rank_type=period, ranking_date=datetime.today(), data_source=self.rank_list_javmenu_censored_day, retrieval_time=datetime.now())
            
            self.__db_insert_javranking(javranking)
            logger.info("[JavScraper][javmenu]已写入数据库[{}/{}]: {}".format(ind+1, len(javlist), str(javranking)))

        self.__add_scrape_history(datetime.now, self.rank_list_javmenu_censored_day, "成功抓取 %s 条数据" % len(javlist))
        logger.info("[JavScraper][javmenu]JavMenu有码日榜抓取任务结束")
            

    def __db_insert_javranking(self, jav_ranking: JavRanking):
        if jav_ranking is None: return
        if self._cnx is None: return

        cursor = self._cnx.cursor()

        # 准备SQL插入语句
        sql = """
        INSERT INTO jav_ranking (
            av_number, av_title, av_cover, release_date, is_downloadable, has_subtitle, ranking_type, ranking_date, ranking, data_source, has_code, retrieval_time
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """

        # 执行SQL插入语句
        cursor.execute(sql, (jav_ranking.av_number, jav_ranking.av_title, jav_ranking.av_cover, jav_ranking.release_date, jav_ranking.is_downloadable, jav_ranking.has_subtitle, jav_ranking.ranking_type, jav_ranking.ranking_date, jav_ranking.ranking, jav_ranking.data_source, jav_ranking.has_code, jav_ranking.retrieval_time))

        # 提交事务
        self._cnx.commit()

    def __format_date(self, date_str):
        if date_str is None: return None
        date_format = "%Y-%m-%d"
        date_object = datetime.strptime(date_str, date_format).date()
        return date_object 

    def __add_scrape_history(self, time, source, info):
        history = self.get_data("scrape_history")
        if history is None: history = []
        history.append({
            "time": time,
            "source": source,
            "info": info
        })
        sorted(history, key=lambda x: x["time"], reverse=True)
        self.save_data(history)

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))