import re
import time
from urllib import parse
from pathlib import Path
import requests
import re
from app.utils.http import RequestUtils
from app.log import logger
from app.core.config import settings
from app.helper.cookiecloud import CookieCloudHelper

from .javmenu import is_jav
from .javbus import JavbusWeb
from .web115 import Py115Web

import py115
from py115.types import Credential

class Jav115:

    client_115 = None
    storage_115 = None
    offline_115 = None
    cookiecloud = None

    max_try_cnt = 10
    wait_second = 3

    code = ""

    def __init__(self) -> None:

        self.cookiecloud = CookieCloudHelper(
            server=settings.COOKIECLOUD_HOST,
            key=settings.COOKIECLOUD_KEY,
            password=settings.COOKIECLOUD_PASSWORD
        )
        cookies, msg = self.cookiecloud.download()
        if msg != "":
            logger.error("获取115 cookies失败：" + msg)
        cookies_115 = cookies['115.com'] if '115.com' in cookies else ""
        # CID=xxx;SEID=xxx;UID=xxx;USERSESSIONID=xxx;acw_tc=xxx;acw_tc=xxx;acw_tc=xx',
        uid, cid, seid = self.parse_115_cookies(cookies_115)
        self.client_115 = py115.connect(credential=Credential(uid=uid, cid=cid, seid=seid))
        self.client_web_115 = Py115Web(cookie=cookies_115)
        self.storage_115 = self.client_115.storage()
        self.offline_115 = self.client_115.offline()
        self.javbusWeb = JavbusWeb()

    def parse_115_cookies(self, cookies):
        key_value_pairs = cookies.split(';')
        result = {}
        for pair in key_value_pairs:
            key, value = pair.split('=')
            result[key] = value
        if 'CID' in result and 'SEID' in result and 'UID' in result:
            return result['UID'], result['CID'], result['SEID']
        else:
            return "","",""

    def _search_from_javbus(self):
        jav_info = self.javbusWeb.search_jav_by_code(self.code)
        if not jav_info:
            return None, None
        if not jav_info['stars'] or len(jav_info['stars']) == 0:
            actor = 'unknown'
        else:
            actor = jav_info['stars'][0]['starName']
        logger.info("[javbus] 番号{}信息：[{}] {} {}".format(jav_info['id'], actor, jav_info['date'], jav_info['title']))
        logger.info("[javbus] 磁力链接：")
        for i, m in enumerate(jav_info['magnets']):
            logger.info("{}: {} {} {}".format(i + 1, m['title'], m['size'], m['shareDate'], m['link']))
        magnet = jav_info['magnet']
        return magnet, jav_info
    
    def search_and_download_jav_115(self, code):
        self.code = code.upper()
        logger.info("开始搜索番号:%s" % self.code)
        jav_115_file = self.client_web_115.searchjav(self.code)
        
        if not jav_115_file:
            logger.info('[crawler] 115中不存在该资源，开始搜索下载...' )
            magnet_info, jav_info = self._search_from_javbus()
            if not magnet_info:
                logger.warning('[crawler] 搜索jav数据失败!')
                return None
            
            logger.info('[crawler] 获得%s的磁力链接：%s' % (self.code, magnet_info['link']) )
            logger.info('[115] 将离线磁力链接至115...')
            
            jav_path = "/jav/"
            if not jav_info['stars'] or len(jav_info['stars']) == 0:
                actor = 'unknown'
            else:
                actor = jav_info['stars'][0]['starName']
            target_path = jav_path + actor
            new_name = "{}{} {}".format(jav_info['date'], ' 【中文字幕】' if magnet_info['hasSubtitle'] else '', jav_info['title'])
            ret = self._upload_offline_115(magnet=magnet_info['link'], target_path=target_path, new_name=new_name, clear_dir=True)
            if ret == False:
                logger.warning('[115] 115离线失败! ')
                return
            else:
                jav_115_file = ret
                if jav_115_file:
                    logger.info('[115] 正在解析:%s' % ret.get('n', ''))
                else:
                    logger.info('[115] 115离线磁力完成, 等待刷新...')
        else:
            logger.info('[115] 在115中找到资源:%s' % jav_115_file.get('n', ''))
            
        try_cnt = 0
        while not jav_115_file:
            try_cnt += 1
            if try_cnt >= self.max_try_cnt:
                break
            time.sleep(self.wait_second)
            jav_115_file = self.client_web_115.searchjav(self.code)
        if not jav_115_file:
            logger.warning('[115] 获取115资源超时，%s 处理失败' % self.code)
            return None
        
        pick_code = jav_115_file.get("pc")
        return self.storage_115.request_download(pickcode=pick_code)
    
    
    def _upload_offline_115(self, magnet, target_path, new_name=None, clear_dir=False):
        logger.info("[115] 离线路径:%s" % target_path)
        ret, tid = self.client_web_115.getdirid(target_path)
        # 目录不存在
        if not ret:
            logger.info("[115] 创建目标目录:%s..." % target_path)
            ret = self.client_web_115.mkdir(target_path)
            if not ret:
                logger.warning("[115] 离线失败, %s目标目录创建失败!" % target_path)
                return False
            logger.info("[115] 创建目标目录创建完成.")
        ret, hash = self.client_web_115.addtask(target_path, magnet)
        if not ret:
            if "验证账号" in hash:
                # webbrowser.open("https://captchaapi.115.com/?ac=security_code&type=web&cb=Close911_{}000".format(int(time.time())))
                logger.error("[115] 离线提交失败, {}".format(hash))
                pass
            else:
                logger.warning("[115] 离线提交失败, {}".format(hash))
            return False
        logger.info("[115] 离线磁力提交成功. {}".format(hash))
        cnt = 0
        target_cid = None
        while not target_cid:
            cnt += 1
            if cnt > self.max_try_cnt:
                logger.info("[115] 检查离线任务超时, 放弃等待.")
                return True
            time.sleep(self.wait_second)
            target = self.client_web_115.task_lists(magnet=magnet)
            target_cid = target['file_id'] if target else None
            
        target_dir = self.client_web_115.dir_list(target_cid)
        
        if clear_dir:
            # 清理大小小于888MB的文件
            del_fids = [i.get("fid") for i in target_dir if int(i.get('s', 931135489)) < 931135488]
            flag = self.client_web_115.batch_del(target_cid, del_fids)
            if flag:
                logger.info("[115] 成功删除{}个垃圾文件(<888MB)".format(len(del_fids)))
            else:
                logger.warning("[115] {}个垃圾文件删除失败!".format(len(del_fids)))
        
        if new_name:
            new_name = new_name[:250]
            new_fids = [target_cid]
            new_fnames = [new_name]
        
            total_files = [i for i in target_dir if int(i.get('s', 931135488)) > 931135488]
            ico_dict = {}
            for item in total_files:
                new_fids.append(item['fid'])
                if item['ico'] not in ico_dict:
                    ico_dict[item['ico']] = 1
                    new_fnames.append("{}.{}".format(new_name, item['ico']))
                else:
                    new_fnames.append("{} ({}).{}".format(new_name, ico_dict[item['ico'], item['ico']]))
                ico_dict[item['ico']] += 1
                
            flag = self.client_web_115.batch_rename(new_fids, new_fnames)
            if flag:
                logger.info("[115] 文件重命名成功：{}".format(new_name))
            else:
                logger.warning("[115] 文件重命名失败!")
        
        target_dir = self.client_web_115.dir_list(target_cid)
        return sorted(target_dir, key=lambda x:(x.get('class', '')=='avi', x.get('s', 0)),reverse=True)[0]
        