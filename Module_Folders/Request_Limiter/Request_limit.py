import time
import threading

import tiktoken_ext  #必须导入这两个库，否则打包后无法运行
from tiktoken_ext import openai_public
import tiktoken #需要安装库pip install tiktoken




# 请求限制器
class Request_Limiter():
    def __init__(self,configurator):

        # TPM相关参数
        self.max_tokens = 0  # 令牌桶最大容量
        self.remaining_tokens = 0 # 令牌桶剩余容量
        self.tokens_rate = 0 # 令牌每秒的恢复速率
        self.last_time = time.time() # 上次记录时间

        # RPM相关参数
        self.last_request_time = 0  # 上次记录时间
        self.request_interval = 0  # 请求的最小时间间隔（s）
        self.lock = threading.Lock()

        self.configurator = configurator


    # 设置限制器的参数
    def set_limit(self,max_tokens,TPM,RPM):
        # 将TPM转换成每秒tokens数
        TPs = TPM / 60

        # 将RPM转换成请求的最小时间间隔(S)
        request_interval =  60 / RPM

        # 设置限制器的TPM参数
        self.max_tokens = max_tokens  # 令牌桶最大容量
        self.remaining_tokens = max_tokens # 令牌桶剩余容量
        self.tokens_rate = TPs # 令牌每秒的恢复速率      

        # 设置限制器的RPM参数
        self.request_interval =request_interval  # 请求的最小时间间隔（s）


    def RPM_limit(self):
        with self.lock:
            current_time = time.time() # 获取现在的时间
            time_since_last_request = current_time - self.last_request_time # 计算当前时间与上次记录时间的间隔
            if time_since_last_request < self.request_interval: 
                # print("[DEBUG] Request limit exceeded. Please try again later.")
                return False
            else:
                self.last_request_time = current_time
                return True



    def TPM_limit(self, tokens):
        now = time.time() # 获取现在的时间
        tokens_to_add = (now - self.last_time) * self.tokens_rate #现在时间减去上一次记录的时间，乘以恢复速率，得出这段时间恢复的tokens数量
        self.remaining_tokens = min(self.max_tokens, self.remaining_tokens + tokens_to_add) #计算新的剩余容量，与最大容量比较，谁小取谁值，避免发送信息超过最大容量
        self.last_time = now # 改变上次记录时间

        if tokens > self.remaining_tokens:
            #print("[DEBUG] 已超过剩余tokens：", tokens,'\n' )
            return False
        else:
           # print("[DEBUG] 数量足够，剩余tokens：", tokens,'\n' )
            return True

    def RPM_and_TPM_limit(self, tokens):
        if self.RPM_limit() and self.TPM_limit(tokens):
            # 如果能够发送请求，则扣除令牌桶里的令牌数
            self.remaining_tokens = self.remaining_tokens - tokens
            return True
        else:
            return False



    # 计算消息列表内容的tokens的函数
    def num_tokens_from_messages(self,messages):
        """Return the number of tokens used by a list of messages."""
        try:
            encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        except KeyError:
            print("Warning: model not found. Using cl100k_base encoding.")
            encoding = tiktoken.get_encoding("cl100k_base")

        tokens_per_message = 3
        tokens_per_name = 1
        num_tokens = 0
        for message in messages:
            num_tokens += tokens_per_message
            for key, value in message.items():
                #如果value是字符串类型才计算tokens，否则跳过，因为AI在调用函数时，会在content中回复null，导致报错
                if isinstance(value, str):
                    num_tokens += len(encoding.encode(value))
                if key == "name":
                    num_tokens += tokens_per_name
        num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
        return num_tokens


    # 计算单个字符串tokens数量函数
    def num_tokens_from_string(self,string):
        """Returns the number of tokens in a text string."""
        encoding = tiktoken.get_encoding("cl100k_base")
        num_tokens = len(encoding.encode(string))
        return num_tokens