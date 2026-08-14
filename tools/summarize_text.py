import os
import sys
# Add the project root to the path to resolve module imports
sys.path.append(r"C:\Users\z\Desktop\project\AiBrowserForSmaLLM")
from llm.llm import LlamaCppClient

# The user prompt for summarization in Chinese
SUMMARY_SYSTEM_PROMPT = "用几句话概括这一段情节。"

def call_llm_api(client: LlamaCppClient, text_chunk: str) -> str:
    """
    通过 LLM 客户端调用 API 来总结给定的文本块。
    """
    print("--- 正在调用 LLM API 进行摘要 ---")
    # 使用 client.chat(prompt, image=None) 调用 API
    # 提示词：要求LLM以中文进行专业的总结
    prompt = f"{SUMMARY_SYSTEM_PROMPT}\n\n---\n{text_chunk}"
    
    # 调用 client.chat，无图像输入，只传文本
    summary = client.chat(prompt, image=None)
    return summary


def chunk_text(text: str, chunk_size: int = 1000) -> list[str]:
    """
    将文本分割成指定大小的块。
    """
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        chunks.append(chunk)
        start = end
    return chunks


def process_and_summarize(client: LlamaCppClient, input_file_path: str, output_file_path: str, chunk_size: int = 1000):
    """
    读取大型文本文件，分割成块，使用 LLM 摘要每个块，并将所有摘要写入输出文件。
    """
    print(f"开始处理文件: {input_file_path}")
    
    if not os.path.exists(input_file_path):
        print(f"错误：找不到输入文件 {input_file_path}")
        return

    try:
        with open(input_file_path, 'r', encoding='utf-8') as f:
            full_text = f.read()
    except Exception as e:
        print(f"读取文件时发生错误: {e}")
        return

    # 1. 分块
    text_chunks = chunk_text(full_text, chunk_size)
    print(f"文本已成功分割成 {len(text_chunks)} 个片段。")

    all_summaries = []
    
    # 2. 摘要每个片段
    for i, chunk in enumerate(text_chunks):
        print(f"正在处理片段 {i+1}/{len(text_chunks)}...")
        try:
            # 调用 LLM API
            summary = call_llm_api(client, chunk)
            all_summaries.append(f"--- 第 {i+1} 部分摘要 ---\n{summary}\n")
        except Exception as e:
            print(f"摘要片段 {i+1} 失败: {e}")
            all_summaries.append(f"--- 第 {i+1} 部分摘要 ---\n[错误: 摘要失败]\n")

    # 3. 写入最终输出
    final_output = "\n".join(all_summaries)

    try:
        with open(output_file_path, 'w', encoding='utf-8') as f:
            f.write(final_output)
        print("\n✅ 所有片段摘要完成，结果已成功保存到:")
        print(f"{output_file_path}")
    except Exception as e:
        print(f"写入输出文件时发生错误: {e}")


if __name__ == "__main__":
    # --- 用户配置开始 ---
    
    # 1. 定义输入文件路径
    INPUT_FILE = r"C:\Users\z\Downloads\《丧尸村镇求生指南》作者：冻青山+(1).txt"  # <-- Updated input file
    
    # 2. 定义输出文件路径
    OUTPUT_FILE = "summary_output.txt"
    
    # --- 用户配置结束 ---
    
    # 实例化 LLM 客户端
    # 默认使用 llm.py 中配置的基地址和超时时间
    try:
        client = LlamaCppClient()
    except Exception as e:
        print(f"初始化 LLM 客户端失败，请确保 llm.py 可用且配置正确。错误: {e}")
        sys.exit(1)

    # 执行处理流程
    process_and_summarize(client, INPUT_FILE, OUTPUT_FILE)