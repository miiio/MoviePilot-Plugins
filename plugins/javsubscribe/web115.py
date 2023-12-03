from __future__ import unicode_literals

import re
import time
from urllib import parse

import requests
import re
from app.utils.http import RequestUtils
from app.log import logger
import json

class Py115Web:
    cookie = None
    user_agent = None
    req = None
    uid = ''
    sign = ''
    err = None

    def __init__(self, cookie):
        # if cookie is None:
        #     cookie_cloud = PyCookieCloud(self.COOKIE_CLOUD_URL, self.COOKIE_UUID, self.COOKIE_PASSWORD)
        #     cookies = cookie_cloud.get_decrypted_data()['115.com']
        #     cookie = ';'.join([i['name']+'='+i['value'] for i in cookies if i['domain']=='.115.com'])
        self.cookie = cookie
        headers = {}
        headers['accept-language'] = 'zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7'
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        headers['user-agent'] = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36'
        self.req = RequestUtils(cookies=self.cookie, session=requests.Session(), headers=headers)

    # 登录
    def login(self):
        # if not self.getuid():
        #     return False
        if not self.getsign():
            return False
        return True

    # 获取目录ID
    def getdirid(self, tdir):
        tdir = tdir.strip()
        if tdir == '/':
            return True, 0
        try:
            url = "https://webapi.115.com/files/getid?path=" + parse.quote(tdir or '/')
            p = self.req.get_res(url=url)
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = "获取目录 [{}]ID 错误：{}".format(tdir, rootobject["error"])
                    return False, ''
                return rootobject.get("id") != 0, rootobject.get("id")
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "getdirid 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False, ''

    # 获取sign
    def getsign(self):
        try:
            self.sign = ''
            url = "https://115.com/?ct=offline&ac=space&_=" + str(round(time.time() * 1000))
            p = self.req.get_res(url=url)
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = "获取 SIGN 错误：{}".format(rootobject.get("error_msg", ''))
                    logger.warn("[web115] " + self.err)
                    return False
                self.sign = rootobject.get("sign")
                return True
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False

    # 获取UID
    def getuid(self):
        try:
            self.uid = ''
            url = "https://webapi.115.com/files?aid=1&cid=0&o=user_ptime&asc=0&offset=0&show_dir=1&limit=30&code=&scid=&snap=0&natsort=1&star=1&source=&format=json"
            p = self.req.get_res(url=url)
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = "获取 UID 错误：{}".format(rootobject.get("error_msg", ''))
                    logger.warn("[web115] " + self.err)
                    return False
                self.uid = rootobject.get("uid")
                return True
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "getuid 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False

    # 获取任务列表
    def gettasklist(self, page=1):
        try:
            tasks = []
            url = "https://115.com/web/lixian/?ct=lixian&ac=task_lists"
            while True:
                postdata = "page={}&uid={}&sign={}&time={}".format(page, self.uid, self.sign,
                                                                   str(round(time.time() * 1000)))
                p = self.req.post_res(url=url, params=postdata.encode('utf-8'))
                if p:
                    rootobject = p.json()
                    if not rootobject.get("state"):
                        self.err = "获取任务列表错误：{}".format(rootobject["error"])
                        logger.warn("[web115] " + self.err)
                        return False, tasks
                    if rootobject.get("count") == 0:
                        break
                    tasks += rootobject.get("tasks") or []
                    if page >= rootobject.get("page_count"):
                        break
            return True, tasks
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "gettasklist 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False, []

    # 添加任务
    def addtask(self, tdir, content):
        try:
            ret, dirid = self.getdirid(tdir)
            if not ret or (tdir != '/' and dirid == 0):
                return False, '115目录不存在'

            # 转换为磁力
            if re.match("^https*://", content):
                try:
                    p = self.req.get_res(url=content)
                    if p and p.headers.get("Location"):
                        content = p.headers.get("Location")
                except Exception as result:
                    # ExceptionUtils.exception_traceback(result)
                    content = str(result).replace("No connection adapters were found for '", "").replace("'", "")

            url = "https://115.com/web/lixian/?ct=lixian&ac=add_task_url"
            postdata = "url={}&savepath=&wp_path_id={}&uid={}&sign={}&time={}".format(parse.quote(content), dirid,
                                                                                      self.uid, self.sign,
                                                                                      str(round(time.time() * 1000)))
            p = self.req.post_res(url=url, params=postdata.encode('utf-8'))
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = rootobject.get("error_msg", '')
                    logger.warn("[web115] " + self.err)
                    return False, self.err
                return True, rootobject.get("info_hash")
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "addtask 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False, self.err

    # 删除任务
    def deltask(self, thash):
        try:
            url = "https://115.com/web/lixian/?ct=lixian&ac=task_del"
            postdata = "hash[0]={}&uid={}&sign={}&time={}".format(thash, self.uid, self.sign,
                                                                  str(round(time.time() * 1000)))
            p = self.req.post_res(url=url, params=postdata.encode('utf-8'))
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = rootobject.get("error_msg", '')
                    logger.warn("[web115] " + self.err)
                    return False
                return True
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "deltask 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False

    # 根据ID获取文件夹路径
    def getiddir(self, tid):
        try:
            path = '/'
            url = "https://aps.115.com/natsort/files.php?aid=1&cid={}&o=file_name&asc=1&offset=0&show_dir=1&limit=40&code=&scid=&snap=0&natsort=1&record_open_time=1&source=&format=json&fc_mix=0&type=&star=&is_share=&suffix=&custom_order=0".format(
                tid)
            p = self.req.get_res(url=url)
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = "获取 ID[{}]路径 错误：{}".format(id, rootobject["error"])
                    logger.warn("[web115] " + self.err)
                    return False, path
                patharray = rootobject["path"]
                for pathobject in patharray:
                    if pathobject.get("cid") == 0:
                        continue
                    path += pathobject.get("name") + '/'
                if path == "/":
                    self.err = "文件路径不存在"
                    logger.warn("[web115] " + self.err)
                    return False, path
                return True, path
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "getiddir 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False, '/'

    def adddir(self, pid, cname):
        # try:
        url = "https://webapi.115.com/files/add"
        # postdata = "pid={}&cname={}".format(pid, cname)
        postdata = {"pid": pid, "cname": cname}
        p = self.req.post_res(url=url, params=postdata)
        if p:
            print(p.text)
            rootobject = p.json()
            if not rootobject.get("state"):
                self.err = rootobject.get("error_msg", '')
                logger.warn("[web115] " + self.err)
                return False
            return True
        # except Exception as result:
        #     # ExceptionUtils.exception_traceback(result)
        #     self.err = "adddir 异常错误：{}".format(result)
        #     logger.warn("[web115] " + self.err)
        return False
    
    def getm3u8(self, pid):
        if not pid:
            return False, ''
        if pid.startswith('https://v.anxia.com/?pickcode='):
            pid = pid[30:]
        try:
            url = "https://115.com/api/video/m3u8/" + pid + ".m3u8"
            p = self.req.get_res(url=url).text
            if p:
                dataList = p.split('\n')
                m3u8 = []
                temp = '"YH"|原画|"BD"|4K|"UD"|蓝光|"HD"|超清|"SD"|高清|"3G"|标清'
                txt = temp.split('|')
                for i in range(6):
                    for j,e in enumerate(dataList):
                        if e.find(txt[i*2]) != -1:
                            m3u8.append({'name': txt[i*2+1], 'url': dataList[j+1].replace('\r', ''), 'type': 'hls'})
                            
                return True, m3u8
            else:
                return False, "播放失败，视频未转码！"
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "getm3u8 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False, self.err
    
    def search_media(self, keyword):
        try:
            url = "https://webapi.115.com/files/search?search_value={}&format=json".format(keyword)
            p = self.req.get_res(url=url)
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = rootobject.get("error_msg", '')
                    logger.warn("[web115] " + self.err)
                    return None
                for item in rootobject.get('data', []):
                    if item.get('play_long') and item.get('n'):
                        return item
                return None
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "search_media 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return None
    
    def searchjav(self, javid):
        if not javid:
            return None
        javid2 = javid.replace('-', "")
        javid3 = javid.replace('-', "00")
        javid4 = javid.replace('-', "0")
        reg = '{}|{}|{}|{}'.format(javid,javid2,javid3,javid4)
        try:
            url = "https://webapi.115.com/files/search?search_value={}%20{}%20{}%20{}&format=json".format(javid,javid2,javid3,javid4)
            p = self.req.get_res(url=url)
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = rootobject.get("error_msg", '')
                    logger.warn("[web115] " + self.err)
                    return None
                for item in rootobject.get('data', []):
                    if item.get('play_long') and item.get('n') and re.search(reg.upper(), item.get('n').upper()):
                        # return 'https://v.anxia.com/?pickcode=' + item.get('pc')
                        return item
                return None
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "searchjav 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return None
    
    def task_lists(self, page=1, magnet=None):
        try:
            url = "https://115.com/web/lixian/?ct=lixian&ac=task_lists&limit=115&page={}".format(page)
            p = self.req.get_res(url=url)
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = rootobject.get("error_msg", '')
                    logger.warn("[web115] " + self.err)
                    return None
                if magnet is None:
                    return rootobject.get('tasks', [])
                else:
                    for item in rootobject.get('tasks', []):
                        if item['url'] == magnet:
                            return item
                    return None
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "task_lists 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return None
    
    def dir_list(self, dirid):
        try:
            url = "https://webapi.115.com/files"
            postdata = "aid=1&cid={}&o=user_utime&asc=0&offset=0&show_dir=1&limit=115&code=&scid=&snap=0&natsort=1&record_open_time=1&count_folders=1&source=&format=json".format(dirid)
            p = self.req.get_res(url=url, params=postdata.encode('utf-8'))
            if p:
                rootobject = p.json()
                if not rootobject.get("state") or not rootobject.get("data"):
                    self.err = rootobject.get("error_msg", '')
                    logger.warn("[web115] " + self.err)
                    return None
                return rootobject.get("data", None)
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "dir_list 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return None
    
    # 批量删除
    def batch_del(self, pid, fids):
        try:
            url = "https://webapi.115.com/rb/delete"
            postdata = "&".join(['fid[{}]={}'.format(i, fids[i]) for i in range(len(fids))])
            postdata = "pid={}&ignore_warn=1&{}".format(pid, postdata)
            p = self.req.post_res(url=url, params=postdata.encode('utf-8'))
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = rootobject.get("error_msg", '')
                    logger.warn("[web115] " + self.err)
                    return False
                return True
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "batch_del 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False
    
    # 文件重命名
    def batch_rename(self, fids, fnames):
        if len(fids) != len(fnames):
            return False
        try:
            url = "https://webapi.115.com/files/batch_rename"
            postdata = "&".join(['files_new_name[{}]={}'.format(fids[i], fnames[i]) for i in range(len(fids))])
            p = self.req.post_res(url=url, params=postdata.encode('utf-8'))
            if p:
                rootobject = p.json()
                if not rootobject.get("state"):
                    self.err = rootobject.get("error_msg", '')
                    logger.warn("[web115] " + self.err)
                    return False
                return True
        except Exception as result:
            # ExceptionUtils.exception_traceback(result)
            self.err = "batch_rename 异常错误：{}".format(result)
            logger.warn("[web115] " + self.err)
        return False
    
    def mkdir(self, target_dir):
        target_dir = target_dir.strip()
        flag, pid = self.getdirid(target_dir)
        if flag:
            return pid
        # /jav/xxx/bbb/ccc
        dirs = target_dir.split('/')
        cur_path = ''
        flag, cur_pid = self.getdirid('/')
        for item in dirs:
            cur_path += '/' + item
            flag, pid = self.getdirid(cur_path)
            if not flag:
                ret = self.adddir(cur_pid, item)
                if not ret:
                    return None
                _, pid = self.getdirid(cur_path)
            cur_pid = pid    
        
        return cur_pid
        
        