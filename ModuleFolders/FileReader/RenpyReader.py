import re
from pathlib import Path
from typing import List, Optional, Tuple

from ModuleFolders.Cache.CacheFile import CacheFile
from ModuleFolders.Cache.CacheItem import CacheItem
from ModuleFolders.Cache.CacheProject import ProjectType
from ModuleFolders.FileReader.BaseReader import (
    BaseSourceReader,
    InputConfig,
    PreReadMetadata
)


class RenpyReader(BaseSourceReader):
    """
    读取 rpy 文件并提取翻译条目，支持下面格式:
    1. old "..." / new "..."
    2. # tag "..." / tag "..."
    3. # "..." / "..."
    4. 支持 # tag "..." 和 tag "..." 中间存在 voice/video 等指令行。
    能处理文本中包含的双引号。
    """
    def __init__(self, input_config: InputConfig):
        super().__init__(input_config)

    @classmethod
    def get_project_type(cls):
        return ProjectType.RENPY

    @property
    def support_file(self):
        return "rpy"

    # 用于查找潜在标签的正则表达式（字母、数字、下划线、空格，首字母后允许）
    # 允许简单的标签如 'sy' 或复杂的标签如 'narrator happy', 'narrator @ happy'
    TAG_PATTERN = re.compile(r"^\s*([a-zA-Z][\w\s@]*)\s+\"")
    # 用于检查行是否以翻译注释行格式开头的正则表达式
    COMMENT_TRANSLATION_START_PATTERN = re.compile(r"^\s*#\s*")
    # 用于检查行是否以翻译代码行格式开头的正则表达式（标签或仅引号）
    # 这个模式是关键，用于识别一个有效的、可翻译的代码行
    CODE_TRANSLATION_START_PATTERN = re.compile(r"^\s*(?:[a-zA-Z][\w\s@]*\s+)?\"")

    def _extract_quoted_robust(self, line: str) -> Optional[str]:
        """
        提取一行中第一个和最后一个双引号之间的文本。
        如果未找到引号或引号不匹配，则返回 None。
        """
        first_quote_index = line.find('"')
        if first_quote_index == -1:
            return None
        last_quote_index = line.rfind('"')
        # 确保第一个和最后一个引号是不同的字符
        if last_quote_index == first_quote_index:
            return None
        return line[first_quote_index + 1:last_quote_index]

    def _find_next_relevant_line(self, lines: List[str], start_index: int) -> Optional[Tuple[int, str]]:
        """
        【已修改】
        从指定索引开始，查找下一个有效的 Ren'Py 代码翻译行。
        一个有效的代码行是 'tag "..."' 或 '"..."' 的形式。
        它会主动跳过空行、注释、以及不符合翻译格式的指令（如 'voice ...', 'video ...' 等）。
        """
        for i in range(start_index, len(lines)):
            line = lines[i]
            stripped = line.strip()

            # 如果遇到下一个翻译块的定义，停止搜索，因为已经超出了当前条目的范围
            if stripped.startswith("translate "):
                return None

            # 检查该行是否是有效的代码翻译行（例如 `yo ""`）
            # 如果是，则我们找到了目标，立即返回
            if self.CODE_TRANSLATION_START_PATTERN.match(stripped):
                return i, line

            # 其他所有行（如空行、#注释、voice指令、video指令等）都会被自动忽略，循环继续
        
        return None # 如果直到文件末尾都没找到，返回 None

    def on_read_source(self, file_path: Path, pre_read_metadata: PreReadMetadata) -> CacheFile:

        lines = file_path.read_text(encoding=pre_read_metadata.encoding).splitlines()

        entries = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # --- 格式 1: old / new ---
            if stripped.startswith("old "):
                source = self._extract_quoted_robust(line)
                if source is not None:
                    # 搜索对应的 'new' 行
                    found_new = False
                    for j in range(i + 1, len(lines)):
                        next_line = lines[j]
                        next_stripped = next_line.strip()
                        if next_stripped.startswith("new "):
                            translated = self._extract_quoted_robust(next_line)
                            if translated is not None:
                                entries.append({
                                    "source": source,
                                    "translated": translated,
                                    "new_line_num": j, # 'new' 行的行号
                                    "format_type": "old_new",
                                    "tag": None
                                })
                                i = j # 主循环跳过已处理的 'new' 行
                                found_new = True
                                break
                        # 如果遇到另一个 'old' 或不同的结构，则停止搜索
                        elif next_stripped.startswith("old ") or \
                             self.COMMENT_TRANSLATION_START_PATTERN.match(next_stripped) or \
                             self.CODE_TRANSLATION_START_PATTERN.match(next_stripped):
                             break
                    if not found_new:
                         # 如果需要，处理 'old' 没有匹配 'new' 的情况
                         # print(f"警告: 'old' 行 {i+1} 在 {file_path} 中没有匹配的 'new' 行")
                         pass # 或者根据期望的行为附加 translated=None
                i += 1 # 处理完 'old' 或 'old' 无效后，移至下一行

            # --- 格式 2 & 3: 注释行后跟代码行 (已支持 voice/video) ---
            elif self.COMMENT_TRANSLATION_START_PATTERN.match(stripped):
                comment_line_num = i
                comment_line = line
                
                # 查找下一个相关的代码行（现在可以跳过 voice/video 等指令）
                next_line_info = self._find_next_relevant_line(lines, i + 1)

                if next_line_info:
                    code_line_num, code_line = next_line_info
                    code_stripped = code_line.strip()

                    # 尝试从注释行提取源文本
                    # 我们需要排除像 # game/... 这样的元数据行
                    potential_source_line = comment_line.split('#', 1)[-1].lstrip()
                    if not (potential_source_line.startswith("game/") or potential_source_line.startswith("renpy/")):
                        comment_source = self._extract_quoted_robust(comment_line)
                    else:
                        comment_source = None # 这不是一个源文本注释行
                    
                    # 从代码行提取潜在的翻译文本
                    code_text = self._extract_quoted_robust(code_line)

                    if comment_source is not None and code_text is not None:
                        # 检查注释行上的标签（# 之后和 " 之前的部分）
                        comment_tag_match = self.TAG_PATTERN.match(comment_line.split('#', 1)[-1])
                        # 检查代码行上的标签
                        code_tag_match = self.TAG_PATTERN.match(code_stripped)

                        tag = None
                        format_type = None

                        # 情况 2: # tag "..." / tag "..."
                        if comment_tag_match and code_tag_match:
                            comment_tag = comment_tag_match.group(1).strip()
                            code_tag = code_tag_match.group(1).strip()
                            if comment_tag == code_tag:
                                tag = code_tag
                                format_type = "comment_tag"

                        # 情况 3: # "..." / "..." (匹配时不涉及标签)
                        elif potential_source_line.startswith('"') and \
                             code_stripped.startswith('"'):
                             format_type = "comment_no_tag"

                        if format_type:
                            entries.append({
                                "source": comment_source, # 原文在注释中
                                "translated": code_text, # 要翻译的文本在代码行中
                                "new_line_num": code_line_num, # 需要修改的代码行的行号
                                "format_type": format_type,
                                "tag": tag # 如果存在则存储标签（格式 2）
                            })
                            i = code_line_num + 1 # 循环跳过已处理的代码行
                            continue # 跳过默认的增量

            # 如果没有模式匹配或处理，则默认增加
            i += 1

        # 转换为 CacheItem 对象
        items = []
        for entry in entries:
            # 对于注释格式，使用注释中的 source，否则使用 'old' 中的 source
            source_text = entry["source"]
            extra = {
                "new_line_num": entry["new_line_num"],
                "format_type": entry["format_type"],
                "tag": entry.get("tag"),  # 使用 .get() 以确保安全
            }
            item = CacheItem(source_text=source_text, translated_text=entry["translated"], extra=extra)
            items.append(item)
        return CacheFile(items=items)