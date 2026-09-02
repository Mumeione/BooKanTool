import os
import re
import json
import requests
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class Qikan:
    web_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    }

    def __init__(self):
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def http_get(self, url, headers=None, timeout=(5, 30)):
        try:
            response = self.session.get(url, headers=headers or self.web_headers, timeout=timeout)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            print(f"HTTP GET error: {e}")
            return None

    def download_image(self, url, destination):
        content = self.http_get(url)
        if content:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "wb") as f:
                f.write(content)
            return destination
        return None

    def get_magazine_issue(self, url):
        content = self.http_get(url)
        if not content:
            return None

        variables_to_match = ["guid", "year", "issue", "codename", "pagecount"]
        result = {}

        # 提取JavaScript变量
        pattern = re.compile(r'var\s+([^=]+)\s*=\s*"([^"]+)"\s*;')
        matches = pattern.findall(content.decode("utf-8"))
        for var_name, var_value in matches:
            var_name = var_name.strip()
            if var_name in variables_to_match:
                result[var_name] = var_value.strip()

        # 提取标题
        title_match = re.search(r'<p class="maga-tc-title">(.*?)</p>', content.decode("utf-8"))
        if title_match:
            result["title"] = title_match.group(1)

        return result

    def download_image_list(self, info):
        path_temp = f"./{info['year']}_{info['issue']}_{info['codename']}"
        os.makedirs(path_temp, exist_ok=True)

        for page in range(1, int(info["pagecount"]) + 1):
            print(f"\r正在下载第 {page} 页  ", end="")
            url = f"http://www.qikan.com.cn/FReader/h5/handle/originalapi.ashx?year={info['year']}&issue={info['issue']}&codename={info['codename']}&page={page}&types=getbigimages"
            content = self.http_get(url)
            if content:
                try:
                    img_urls = json.loads(content.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    print(f"\n第 {page} 页图片列表解析失败: {e}")
                    continue
                for img_url in img_urls:
                    filename = os.path.basename(img_url.split("?")[0])
                    self.download_image(img_url, os.path.join(path_temp, filename))

                self.splicing_img(path_temp, page)
                print(f"\r已下载完成 {page} 页  ", end="")

    def get_image_paths(self, directory, page):
        """获取指定页的所有图片路径"""
        page_t = str(page).zfill(4)
        image_paths = []
        for file_name in os.listdir(directory):
            if file_name.startswith(f"{page_t}_") and file_name.endswith(".jpg"):
                image_paths.append(os.path.join(directory, file_name))
        image_paths.sort(key=lambda x: (self.extract_row_col(x)[0], self.extract_row_col(x)[1]))
        return image_paths

    def extract_row_col(self, file_path):
        """从文件名中提取行号和列号"""
        base_name = os.path.basename(file_path)
        parts = base_name.split("_")
        row = int(parts[1])
        col = int(parts[2].split(".")[0])
        return (row, col)

    def splicing_img(self, path, page):
        # 获取当前页面的所有图片路径
        image_paths = self.get_image_paths(path, page)
        if not image_paths:
            print("No images found for the specified page.")
            return

        # 按行分组图片
        rows = {}
        for img_path in image_paths:
            row, col = self.extract_row_col(img_path)
            if row not in rows:
                rows[row] = []
            rows[row].append(img_path)

        # 按行号排序行
        sorted_rows = sorted(rows.values(), key=lambda x: self.extract_row_col(x[0])[0])

        # 合并所有图片到画布
        current_y = 0
        merged_image = None
        total_width = 0
        total_height = 0

        for row_imgs in sorted_rows:
            row_width = 0
            row_height = 0
            current_x = 0
            row_imgs_info = []

            # 处理该行的图片
            for img_path in row_imgs:
                try:
                    img = Image.open(img_path)
                    img_width, img_height = img.size
                    row_imgs_info.append((img, img_width, img_height))
                    row_width += img_width
                    row_height = max(row_height, img_height)
                except Exception as e:
                    print(f"Error processing {img_path}: {e}")

            # 计算行的位置
            if merged_image is None:
                merged_image = Image.new("RGB", (row_width, row_height))
                total_width = row_width
                total_height = row_height
            else:
                new_total_width = max(total_width, row_width)
                new_total_height = current_y + row_height
                new_image = Image.new("RGB", (new_total_width, new_total_height))
                new_image.paste(merged_image, (0, 0))
                merged_image = new_image
                total_width = new_total_width
                total_height = new_total_height

            # 粘贴该行的图片到画布
            current_row_x = 0
            current_row_y = current_y
            for img_info in row_imgs_info:
                img, img_width, img_height = img_info
                merged_image.paste(
                    img,
                    (
                        current_row_x,
                        current_row_y,
                        current_row_x + img_width,
                        current_row_y + img_height,
                    ),
                )
                current_row_x += img_width

            current_y += row_height

        # 保存拼接后的图片
        output_path = os.path.join(path, f"{str(page).zfill(4)}.jpg")
        merged_image.crop((0, 0, total_width, total_height)).save(output_path)

    def create_pdf(self, path, page_all, name=None):
        pages = []
        for page in range(1, page_all + 1):
            page_t = str(page).zfill(4)
            img_path = os.path.join(path, f"{page_t}.jpg")

            if not os.path.exists(img_path):
                print(f"跳过缺失页面 {img_path}")
                continue

            try:
                img = Image.open(img_path).convert("RGB")
                pages.append(img)
            except Exception as e:
                print(f"读取图片失败 {img_path}: {str(e)}")

        if not pages:
            print("没有可用页面，PDF 生成失败")
            return

        # 按宽度适配 A4（210mm）设置分辨率，保持与原 fpdf 版本一致的页面尺寸
        a4_width_inch = 210 / 25.4
        resolution = pages[0].width / a4_width_inch

        output_path = f"./{name}.pdf" if name else f"{path}.pdf"
        first, rest = pages[0], pages[1:]
        first.save(output_path, "PDF", save_all=True, append_images=rest, resolution=resolution)
        for img in pages:
            img.close()
        print(f"\n文件被保存在：{output_path}")

    def download_magazine(self, url):
        url = url.replace("/magdetails/", "/original/").replace("/m/", "/")
        info = self.get_magazine_issue(url)
        if not info:
            print("\n解析失败")
            return

        print(f"\n杂志名称：{info.get('title', '')}")
        print(f"页码总数：{info.get('pagecount', 0)}\n")

        required_keys = ("year", "issue", "codename", "pagecount")
        missing = [k for k in required_keys if k not in info]
        if missing:
            print(f"页面解析不完整，缺少字段: {', '.join(missing)}")
            return

        self.download_image_list(info)
        self.create_pdf(
            path=f"./{info['year']}_{info['issue']}_{info['codename']}",
            page_all=int(info["pagecount"]),
            name=info.get("title", None),
        )


if __name__ == "__main__":
    qikan = Qikan()
    url = input("请输入杂志url: ")
    qikan.download_magazine(url)
