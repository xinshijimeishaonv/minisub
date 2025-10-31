# jichang_list.py

import requests
from bs4 import BeautifulSoup
import re
import time
import random
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

# 配置日志
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# User-Agent 池
# User-Agent pool
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.164 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1'
]

def is_valid_hostname(hostname):
    """
    检查主机名是否符合域名规则。
    Check if the hostname is valid according to domain name rules.
    """
    if not hostname or len(hostname) > 255:
        return False
    if hostname[-1] == ".":
        hostname = hostname[:-1] # 移除末尾的点
    allowed = re.compile(r'(?!-)[A-Z\d-]{1,63}(?<!-)$', re.IGNORECASE)
    return all(allowed.match(label) for label in hostname.split("."))

def is_valid_url(url):
    """
    通过检查其结构和主机名来验证URL。
    Validate the URL by checking its structure and hostname.
    """
    # 排除Telegram自身链接，避免无限循环或无关链接
    invalid_prefixes = ('https://t.me', 'http://t.me', 't.me')
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    if not is_valid_hostname(parsed.netloc):
        return False
    # 检查URL是否以无效前缀开头
    return not any(url.startswith(prefix) for prefix in invalid_prefixes)

def clean_url(url):
    """
    从URL中移除末尾的标点符号。
    Remove trailing punctuation from the URL.
    """
    while url and url[-1] in '.,;:!?)':
        url = url[:-1]
    return url

def get_urls_from_html(html):
    """
    从HTML内容中提取并清理URL。
    Extract and clean URLs from HTML content.
    """
    soup = BeautifulSoup(html, 'html.parser')
    # 查找可能包含URL的元素类
    targets = soup.find_all(class_=[
        'tgme_widget_message_text',
        'tgme_widget_message_photo',
        'tgme_widget_message_video',
        'tgme_widget_message_document',
        'tgme_widget_message_poll',
    ])

    urls = set() # 使用集合自动去重

    for target in targets:
        # 从 <a> 标签中提取
        for a_tag in target.find_all('a', href=True):
            href = clean_url(a_tag['href'].rstrip('/')) # 移除末尾斜杠和标点
            if is_valid_url(href):
                urls.add(href)
            else:
                logging.debug(f"从 <a> 标签中发现的无效URL已丢弃: {href}")

        # 从文本内容中提取
        text = target.get_text(separator=' ', strip=True)
        # 查找 http:// 或 https:// 或 www. 开头的URL
        found_urls_in_text = re.findall(r'https?://[^\s<>"\']+|www\.[^\s<>"\']+', text)
        for url_in_text in found_urls_in_text:
            if url_in_text.startswith('www.'):
                url_in_text = 'http://' + url_in_text # 为 www. 开头的URL添加 http://
            url_in_text = clean_url(url_in_text.rstrip('/')) # 移除末尾斜杠和标点
            if is_valid_url(url_in_text):
                urls.add(url_in_text)
            else:
                logging.debug(f"从文本中发现的无效URL已丢弃: {url_in_text}")
    return list(urls) # 返回列表，方便后续处理

def get_urls_from_github_raw(content):
    """
    从GitHub raw内容中提取并清理URL。
    Extract and clean URLs from GitHub raw content.
    """
    urls = set() # 使用集合自动去重
    
    # 按行分割内容
    lines = content.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # 查找 http:// 或 https:// 开头的URL
        found_urls = re.findall(r'https?://[^\s<>"\']+', line)
        for url in found_urls:
            url = clean_url(url.rstrip('/')) # 移除末尾斜杠和标点
            if is_valid_url(url):
                urls.add(url)
            else:
                logging.debug(f"从GitHub raw内容中发现的无效URL已丢弃: {url}")
    
    return list(urls) # 返回列表，方便后续处理

def test_url_connectivity(url, timeout=10):
    """
    测试URL是否可连接。
    Test if the URL is connectable.
    """
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        # 使用 HEAD 请求，只获取头部信息，更快
        response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        return response.status_code == 200 # 200 OK 表示连接成功
    except requests.RequestException:
        return False

def get_next_page_url(html):
    """
    从HTML中提取下一页的URL。
    Extract the next page URL from HTML.
    """
    soup = BeautifulSoup(html, 'html.parser')
    load_more = soup.find('a', class_='tme_messages_more') # 查找"加载更多"按钮
    if load_more and load_more.has_attr('href'):
        # 补全为完整的URL
        return 'https://t.me' + load_more['href']
    return None

def fetch_page(url, timeout=15, max_retries=3):
    """
    抓取页面内容，带重试和随机User-Agent。
    Fetch page content with retries and random User-Agent.
    """
    headers = {'User-Agent': random.choice(USER_AGENTS)}
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status() # 对 4XX/5XX 状态码抛出 HTTPError
            return response.text
        except requests.exceptions.RequestException as e:
            logging.warning(f"在尝试 {attempt + 1}/{max_retries} 次后，抓取 {url} 失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(random.uniform(5, 12 + attempt * 2)) # 每次重试增加延迟
            else:
                logging.error(f"在 {max_retries} 次尝试后，抓取 {url} 最终失败。")
                return None
    return None # 理论上不会执行到这里，因为max_retries耗尽时会直接返回None

def fetch_github_raw(url, timeout=15, max_retries=3):
    """
    抓取GitHub raw内容，带重试和随机User-Agent。
    Fetch GitHub raw content with retries and random User-Agent.
    """
    headers = {'User-Agent': random.choice(USER_AGENTS)}
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status() # 对 4XX/5XX 状态码抛出 HTTPError
            return response.text
        except requests.exceptions.RequestException as e:
            logging.warning(f"在尝试 {attempt + 1}/{max_retries} 次后，抓取GitHub raw {url} 失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(random.uniform(5, 12 + attempt * 2)) # 每次重试增加延迟
            else:
                logging.error(f"在 {max_retries} 次尝试后，抓取GitHub raw {url} 最终失败。")
                return None
    return None # 理论上不会执行到这里，因为max_retries耗尽时会直接返回None

def load_existing_urls(filename='./trial.cfg'):
    """
    从文件中加载已有的URL集合。
    Load existing URLs from a file into a set.
    """
    existing_urls = set()
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                for line in f:
                    url = line.strip()
                    if url:
                        existing_urls.add(url)
            logging.info(f"已从 {filename} 加载 {len(existing_urls)} 个现有URL。")
        except IOError as e:
            logging.error(f"加载现有URL失败 {filename}: {e}")
    else:
        logging.info(f"文件 {filename} 不存在，将创建新文件。")
    return existing_urls

def save_urls_to_file(new_urls, filename='./trial.cfg'):
    """
    将URL保存到文件，并确保文件内容去重。
    Save URLs to a file, ensuring the file content is de-duplicated.
    """
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    
    # 1. 加载现有URL
    existing_urls = load_existing_urls(filename) # 实际上这个函数内部已经有日志了

    # 2. 合并新旧URL并去重
    all_urls = existing_urls.union(new_urls) # 使用集合的union操作，自动去重

    try:
        # 3. 将所有去重后的URL重新写入文件 (覆盖模式 'w')
        with open(filename, 'w', encoding='utf-8') as f:
            for url in sorted(list(all_urls)): # 排序后写入，方便查看
                f.write(url + '\n')
        logging.info(f"所有去重后的URL已保存到 {filename} (总数: {len(all_urls)})")
    except IOError as e:
        logging.error(f"保存URL到 {filename} 失败: {e}")

def scrape_telegram_channels(start_urls, max_pages_per_source=90):
    """
    抓取Telegram频道的机场URL。
    Scrape airport URLs from Telegram channels.
    """
    found_urls = set()
    processed_page_count_total = 0

    for base_url in start_urls:
        logging.info(f"======== 开始处理Telegram来源: {base_url} ========")
        current_url = base_url
        page_count_for_source = 0

        while current_url and page_count_for_source < max_pages_per_source:
            logging.info(f"正在抓取Telegram页面: {current_url} (来源: {base_url}, 第 {page_count_for_source + 1}/{max_pages_per_source} 页)")
            html = fetch_page(current_url) # 这里调用 fetch_page
            if html is None:
                logging.warning(f"无法从 {current_url} 获取内容，停止此来源的抓取。")
                break

            new_urls = get_urls_from_html(html)
            if new_urls:
                logging.info(f"此Telegram页面发现 {len(new_urls)} 个新URL。")
                found_urls.update(new_urls) # 直接添加到总集合中
                logging.info(f"Telegram来源发现的总URL数量: {len(found_urls)}")

            next_page_url = get_next_page_url(html)
            current_url = next_page_url
            page_count_for_source += 1
            processed_page_count_total += 1
            time.sleep(random.uniform(20, 40)) # 随机延迟，避免被封禁

        logging.info(f"======== Telegram来源 {base_url} 处理完毕，共处理 {page_count_for_source} 页 ========")

    logging.info(f"\n======== Telegram来源处理完毕，总页数: {processed_page_count_total} ========")
    logging.info(f"Telegram来源发现的唯一URL总数: {len(found_urls)}")
    
    return found_urls

def scrape_github_sources(github_urls):
    """
    抓取GitHub raw文件的机场URL。
    Scrape airport URLs from GitHub raw files.
    """
    found_urls = set()
    
    for github_url in github_urls:
        logging.info(f"======== 开始处理GitHub来源: {github_url} ========")
        
        content = fetch_github_raw(github_url)
        if content is None:
            logging.warning(f"无法从GitHub raw {github_url} 获取内容，跳过此来源。")
            continue

        new_urls = get_urls_from_github_raw(content)
        if new_urls:
            logging.info(f"GitHub来源 {github_url} 发现 {len(new_urls)} 个新URL。")
            found_urls.update(new_urls) # 直接添加到总集合中
            logging.info(f"GitHub来源发现的总URL数量: {len(found_urls)}")
        else:
            logging.info(f"GitHub来源 {github_url} 未发现有效URL。")

        logging.info(f"======== GitHub来源 {github_url} 处理完毕 ========")
        time.sleep(random.uniform(5, 10)) # 随机延迟，避免请求过于频繁

    logging.info(f"\n======== GitHub来源处理完毕 ========")
    logging.info(f"GitHub来源发现的唯一URL总数: {len(found_urls)}")
    
    return found_urls

def main(start_urls, github_urls, max_pages_per_source=90, max_workers=10):
    """
    控制抓取过程的主函数。
    Main function to control the scraping process.
    """
    # 初始化总的URL集合，并加载文件中已存在的URL，实现跨次运行的去重
    # Initialize the overall URL set and load existing URLs from file for cross-run de-duplication
    overall_found_urls = load_existing_urls('./trial.cfg')
    
    # 1. 抓取Telegram频道
    telegram_urls = scrape_telegram_channels(start_urls, max_pages_per_source)
    overall_found_urls.update(telegram_urls)
    
    # 2. 抓取GitHub raw文件
    github_urls_found = scrape_github_sources(github_urls)
    overall_found_urls.update(github_urls_found)

    logging.info(f"\n======== 所有来源处理完毕 ========")
    logging.info(f"所有来源发现的唯一URL总数: {len(overall_found_urls)}")

    if not overall_found_urls:
        logging.info("没有找到可进行连通性测试的URL。")
    else:
        logging.info("开始并发URL连通性测试...")
        valid_urls_set = set() # 使用集合存储有效的URL
        # 由于 overall_found_urls 是一个集合，map 函数需要一个可迭代对象，将其转换为列表。
        # 同时，为了保留每个URL对应的测试结果，将 zip 迭代器转换为列表或直接处理。
        # 更高效的方式是直接对集合进行 map，然后筛选。
        
        # 将集合转换为列表，以便 ThreadPoolExecutor.map 可以处理
        urls_to_test = list(overall_found_urls) 
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # map 返回一个迭代器，按输入顺序返回结果
            results = executor.map(test_url_connectivity, urls_to_test)
            
            # 遍历原始URL和对应的结果
            for url, is_connectable in zip(urls_to_test, results):
                if is_connectable:
                    valid_urls_set.add(url) # 添加到有效的URL集合中

        logging.info(f"连通性测试完成。有效URL数量: {len(valid_urls_set)}")
        logging.info("正在保存最终有效URL...")
        save_urls_to_file(valid_urls_set, './trial.cfg') # 传入集合
        logging.info("最终结果已保存。")

if __name__ == '__main__':
    # Telegram频道来源
    start_urls_list = [
        'https://t.me/s/jichang_list',
        # 'https://t.me/s/another_channel_example', # 您可以添加更多起始URL
    ]
    
    # GitHub raw文件来源
    github_urls_list = [
        'https://raw.githubusercontent.com/zwrt/Toolbox/refs/heads/main/file/jichang.txt',
        'https://raw.githubusercontent.com/moneyfly1/666/refs/heads/main/trial.cfg',
        'https://raw.githubusercontent.com/moneyfly1/jichangnodes/refs/heads/main/trial.cfg',
        'https://raw.githubusercontent.com/luochen1975/jichangnodes/refs/heads/main/trial.cfg',
        # 可以添加更多GitHub raw文件
    ]
    
    max_pages_to_crawl_per_source = 5 # 每个Telegram来源最多抓取的页数
    concurrent_workers = 15 # 并发测试URL连通性的工作线程数

    main(start_urls_list, github_urls_list, max_pages_to_crawl_per_source, concurrent_workers)
