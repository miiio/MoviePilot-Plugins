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

class JavCrawler(_PluginBase):
    # 插件名称
    plugin_name = "Jav爬虫"
    # 插件描述
    plugin_desc = "自动抓取Jav数据"
    # 插件图标
    plugin_icon = "statistic.png"
    # 插件版本
    plugin_version = "0.0.9.2"
    # 插件作者
    plugin_author = "miiio"
    # 作者主页
    author_url = "https://github.com/miiio"
    # 插件配置项ID前缀
    plugin_config_prefix = "jav_crawler_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 1

    rank_list_javmenu_censored_day = "javmenu:censored:day"
    rank_list_javmenu_censored_week = "javmenu:censored:week"
    rank_list_javmenu_censored_month = "javmenu:censored:month"

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
                self._scheduler.add_job(func=self.crawler, trigger='date',
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

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        :return: 命令关键字、事件、描述、附带数据
        """
        pass
        # return [{
        #     "cmd": "/site_signin",
        #     "event": EventType.PluginAction,
        #     "desc": "站点签到",
        #     "category": "站点",
        #     "data": {
        #         "action": "site_signin"
        #     }
        # }]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API说明"
        }]
        """
        pass
        # return [{
        #     "path": "/signin_by_domain",
        #     "endpoint": self.signin_by_domain,
        #     "methods": ["GET"],
        #     "summary": "站点签到",
        #     "description": "使用站点域名签到站点",
        # }]

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
                        "id": "Javcrawler",
                        "name": "Jav数据抓取",
                        "trigger": CronTrigger.from_crontab(self._cron),
                        "func": self.crawler,
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
                                "id": "Javcrawler",
                                "name": "Jav数据抓取",
                                "trigger": "interval",
                                "func": self.crawler,
                                "kwargs": {
                                    "hours": float(str(cron).strip()),
                                }
                            }]
                        else:
                            logger.error("Jav数据抓取服务启动失败，周期格式错误")
                    else:
                        # 默认0-24 按照周期运行
                        return [{
                            "id": "Javcrawler",
                            "name": "Jav数据抓取",
                            "trigger": "interval",
                            "func": self.crawler,
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
                    "id": f"Javcrawler|{trigger.hour}:{trigger.minute}",
                    "name": "Jav数据抓取",
                    "trigger": "cron",
                    "func": self.crawler,
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
        rank_options = ([
            {
                "title": "JavMenu有码日榜",
                "value": self.rank_list_javmenu_censored_day
            },
            {
                "title": "JavMenu有码周榜",
                "value": self.rank_list_javmenu_censored_week
            },
            {
                "title": "JavMenu有码月榜",
                "value": self.rank_list_javmenu_censored_month
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
                ]
            }
        ], {
            "enabled": False,
            "notify": False,
            "cron": "",
            "onlyonce": False,
            "rank_list": [self.rank_list_javmenu_censored_day, self.rank_list_javmenu_censored_week, self.rank_list_javmenu_censored_month],
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
        crawl_data = self.get_data("crawl_history")
        if crawl_data:
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
                } for data in crawl_data
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

    def crawler(self):
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
        logger.info("[JavCrawler]开始连接mysql数据库 ...")
        
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
            logger.error("[JavCrawler]连接mysql数据库失败 ...")
            return

        logger.info("[JavCrawler]开始执行Jav数据抓取 ...")

        if self._rank_list:
            self.__do_crawl_rank_list(rank_list=self._rank_list)

        
        logger.info("[JavCrawler]断开mysql数据库 ...")
        if self._cnx:
            self._cnx.close()


    def __do_crawl_rank_list(self, rank_list):
        for rank in rank_list:
            if rank == self.rank_list_javmenu_censored_day:
                self.__do_crawl_javmenu_rank(rank_type="censored", period="day", crawl_source=rank)
            elif rank == self.rank_list_javmenu_censored_week:
                self.__do_crawl_javmenu_rank(rank_type="censored", period="week", crawl_source=rank)
            elif rank == self.rank_list_javmenu_censored_month:
                self.__do_crawl_javmenu_rank(rank_type="censored", period="month", crawl_source=rank)

    def __do_crawl_javmenu_rank(self, rank_type: str, period: str, crawl_source: str):
        source = "JavMenu{}{}".format("有码" if rank_type == "censored" else "无码", "日榜" if period == "day" else ("周榜" if period == "week" else "月榜"))
        logger.info("[JavCrawler][javmenu]开始抓取{} ...".format(source))

        ret = self.javmenu.rank_list(type=rank_type, rank_type=period, page=1)

        javlist = ret['jav_list']
        logger.info("[JavCrawler][javmenu]{}抓取完成，共 {} 条数据".format(source, len(javlist)))
        logger.info("[JavCrawler][javmenu]开始写入数据库 ...")
        today = datetime.today()
        nowtime = datetime.now()
        for ind, jav in enumerate(javlist):
            javranking = JavRanking(av_number=jav.get("id", ""), av_title=jav.get("title", ""), av_cover=jav.get("img", ""), release_date=self.__format_date(jav.get("date", None)),
                                    is_downloadable=jav.get('is_downloadable', False), has_subtitle=jav.get('has_subtitle', False), ranking=jav.get('ranking', 0), 
                                    has_code=rank_type=="censored", ranking_type=period, ranking_date=today, data_source=crawl_source, retrieval_time=nowtime)
            
            self.__db_insert_javranking(javranking)
            logger.info("[JavCrawler][javmenu]已写入数据库[{}/{}]: {}".format(ind+1, len(javlist), str(javranking)))

        self.__add_crawl_history(datetime.now(), crawl_source, "成功抓取 %s 条数据" % len(javlist))
        logger.info("[JavCrawler][javmenu]{}抓取任务结束".format(source))
            

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
        try:
            cursor.execute(sql, (jav_ranking.av_number, jav_ranking.av_title, jav_ranking.av_cover, jav_ranking.release_date, jav_ranking.is_downloadable, jav_ranking.has_subtitle, jav_ranking.ranking_type, jav_ranking.ranking_date, jav_ranking.ranking, jav_ranking.data_source, jav_ranking.has_code, jav_ranking.retrieval_time))
            self._cnx.commit()
        except Exception as e:
            self._cnx.rollback()
        finally:
            cursor.close()

    def __format_date(self, date_str):
        if date_str is None: return None
        date_format = "%Y-%m-%d"
        date_object = datetime.strptime(date_str, date_format).date()
        return date_object 

    def __add_crawl_history(self, time:datetime, source, info):
        history_data = self.get_data("crawl_history")
        history = []
        if history_data is not None:
            history = history_data
        history.append({
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "source": source,
            "info": info
        })
        history = sorted(history, key=lambda x: x["time"], reverse=True)
        self.save_data("crawl_history", history)

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