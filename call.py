#!/usr/bin/env python3
"""
通用MCP客户端工具 (作为 claude_link 模块的一部分)
可通过: python -m claude_link.call 使用
"""
import json
import sys
import subprocess
import argparse
import time
import os
import logging
from typing import Dict, Any, Optional


class MCPError(Exception):
    """MCP相关错误"""
    pass


class MCPTimeoutError(MCPError):
    """MCP超时错误"""
    pass


class MCPConnectionError(MCPError):
    """MCP连接错误"""
    pass


class MCPClient:
    """增强的MCP JSON-RPC客户端"""

    def __init__(self, timeout: int = 30, verbose: bool = False):
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self.timeout = timeout
        self.verbose = verbose
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("mcp_client")
        if self.verbose:
            logger.setLevel(logging.DEBUG)
            handler = logging.StreamHandler(sys.stderr)
            formatter = logging.Formatter('[%(levelname)s] %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def _next_id(self) -> str:
        self.request_id += 1
        return str(self.request_id)

    def _create_jsonrpc_message(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        message = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method
        }
        if params:
            message["params"] = params
        return message

    def _send_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if not self.process:
            raise MCPConnectionError("MCP服务器未连接")
        try:
            json_str = json.dumps(message, ensure_ascii=False) + "\n"
            self.logger.debug(f"发送消息: {json_str.strip()}")
            self.process.stdin.write(json_str.encode('utf-8'))
            self.process.stdin.flush()

            import select
            ready, _, _ = select.select([self.process.stdout], [], [], self.timeout)
            if not ready:
                raise MCPTimeoutError(f"服务器响应超时 ({self.timeout}秒)")

            response_line = self.process.stdout.readline()
            if not response_line:
                raise MCPConnectionError("服务器连接中断")

            response_str = response_line.decode('utf-8').strip()
            self.logger.debug(f"收到响应: {response_str}")
            try:
                response = json.loads(response_str)
                if "error" in response:
                    error = response["error"]
                    error_msg = f"MCP错误 [{error.get('code', 'unknown')}]: {error.get('message', 'Unknown error')}"
                    if 'data' in error:
                        error_msg += f" - {error['data']}"
                    raise MCPError(error_msg)
                return response
            except json.JSONDecodeError as e:
                raise MCPError(f"无效的JSON响应: {e} - 原始响应: {response_str}")
        except (BrokenPipeError, OSError) as e:
            raise MCPConnectionError(f"连接错误: {e}")
        except Exception as e:
            if isinstance(e, (MCPError, MCPTimeoutError, MCPConnectionError)):
                raise
            raise MCPError(f"消息发送失败: {e}")

    def _send_notification(self, message: Dict[str, Any]) -> None:
        if not self.process:
            raise MCPConnectionError("MCP服务器未连接")
        try:
            message = dict(message)
            message.pop("id", None)
            json_str = json.dumps(message, ensure_ascii=False) + "\n"
            self.logger.debug(f"发送通知: {json_str.strip()}")
            self.process.stdin.write(json_str.encode('utf-8'))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPConnectionError(f"连接错误: {e}")
        except Exception as e:
            raise MCPError(f"通知发送失败: {e}")

    def connect_to_server(self, server_command: str) -> bool:
        try:
            self.process = subprocess.Popen(
                server_command.split(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False
            )
            init_message = self._create_jsonrpc_message("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
                "clientInfo": {"name": "claude-link.call", "version": "1.0.0"}
            })
            response = self._send_message(init_message)
            if "error" in response:
                print(f"初始化失败: {response['error']}", file=sys.stderr)
                return False
            initialized_message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            self._send_notification(initialized_message)
            print("成功连接到MCP服务器", file=sys.stderr)
            return True
        except Exception as e:
            print(f"连接失败: {e}", file=sys.stderr)
            return False

    def call_method(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        message = self._create_jsonrpc_message(method, params)
        return self._send_message(message)

    def disconnect(self):
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process = None


def expand_env_vars(text: str) -> str:
    import re
    def replace_var(match):
        var_name = match.group(1)
        default_value = match.group(2) if match.group(2) else ""
        return os.environ.get(var_name, default_value)
    pattern = r'\$\{([^}:]+)(?::([^}]*))?\}'
    return re.sub(pattern, replace_var, text)


def parse_params(params_input: str) -> Dict[str, Any]:
    if not params_input:
        return {}
    if params_input.startswith('@'):
        file_path = params_input[1:]
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                params_str = f.read().strip()
        except FileNotFoundError:
            print(f"参数文件不存在: {file_path}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"读取参数文件失败: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        params_str = params_input
    params_str = expand_env_vars(params_str)
    try:
        return json.loads(params_str)
    except json.JSONDecodeError as e:
        print(f"参数解析失败: {e}", file=sys.stderr)
        print(f"原始参数: {params_str}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="通用MCP客户端工具 (claude_link.call)",
        epilog="""
示例用法:
  python -m claude_link.call --server "python -m claude_link" --method tools/list
  python -m claude_link.call --server "claude-link" --method inject_text --params '{"target_id":"%5","text":"hi"}'
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--server", required=True, help="MCP服务器命令 (例如: 'python -m claude_link' 或 'claude-link')")
    parser.add_argument("--method", required=True, help="要调用的方法名")
    parser.add_argument("--params", default="{}", help="JSON格式的参数，支持 @file.json 从文件读取，支持环境变量 ${VAR}")
    parser.add_argument("--output", choices=["json", "text", "result"], default="json", help="输出格式")
    parser.add_argument("--timeout", type=int, default=30, help="超时时间(秒)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出模式")
    parser.add_argument("--retry", type=int, default=1, help="重试次数")

    args = parser.parse_args()

    if args.retry < 1:
        print("重试次数必须至少为1", file=sys.stderr)
        sys.exit(1)

    for attempt in range(args.retry):
        if args.verbose and attempt > 0:
            print(f"重试第 {attempt} 次...", file=sys.stderr)
        client = MCPClient(timeout=args.timeout, verbose=args.verbose)
        try:
            if not client.connect_to_server(args.server):
                raise MCPConnectionError("无法连接到MCP服务器")
            params = parse_params(args.params)
            response = client.call_method(args.method, params)
            if args.output == "json":
                print(json.dumps(response, indent=2, ensure_ascii=False))
            elif args.output == "result":
                if "result" in response:
                    result = response["result"]
                    if isinstance(result, (dict, list)):
                        print(json.dumps(result, indent=2, ensure_ascii=False))
                    else:
                        print(result)
                else:
                    print("无结果返回", file=sys.stderr)
                    sys.exit(1)
            else:
                if "result" in response:
                    print(f"成功: {response['result']}")
                else:
                    print("操作完成")
            break
        except KeyboardInterrupt:
            print("\n操作被中断", file=sys.stderr)
            sys.exit(1)
        except (MCPError, MCPTimeoutError, MCPConnectionError) as e:
            if args.verbose:
                print(f"尝试 {attempt + 1} 失败: {e}", file=sys.stderr)
            if attempt == args.retry - 1:
                print(f"执行失败: {e}", file=sys.stderr)
                sys.exit(1)
            time.sleep(1)
        except Exception as e:
            print(f"未预期错误: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)
        finally:
            client.disconnect()


if __name__ == "__main__":
    main()
