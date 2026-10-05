import argparse
import os
import re
import statistics
import sys


def process_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(f"Lỗi khi đọc file {file_path}: {e}")
        return [], []

    # Thêm newline ở đầu để dễ match các pattern bắt đầu bằng \n
    text = '\n' + text

    # Regex pattern cho Chương và Điều
    chuong_pattern = re.compile(r'\n\s*Chương\s+[IVXLCDM0-9]+\b', re.IGNORECASE)
    dieu_pattern = re.compile(r'\n\s*Điều\s+\d+\b', re.IGNORECASE)

    chuong_matches = list(chuong_pattern.finditer(text))
    dieu_matches = list(dieu_pattern.finditer(text))

    chuong_lengths = []
    for i in range(len(chuong_matches)):
        start = chuong_matches[i].start()
        end = chuong_matches[i+1].start() if i + 1 < len(chuong_matches) else len(text)
        chunk = text[start:end].strip()
        chuong_lengths.append(len(chunk))

    dieu_lengths = []
    for i in range(len(dieu_matches)):
        start = dieu_matches[i].start()
        
        next_dieu_start = dieu_matches[i+1].start() if i + 1 < len(dieu_matches) else len(text)
        next_chuong_start = len(text)
        for c in chuong_matches:
            if c.start() > start:
                next_chuong_start = c.start()
                break
                
        end = min(next_dieu_start, next_chuong_start)
        chunk = text[start:end].strip()
        dieu_lengths.append(len(chunk))

    return chuong_lengths, dieu_lengths

def analyze_law_structure(target_path):
    files_to_process = []
    if os.path.isfile(target_path):
        files_to_process.append(target_path)
    elif os.path.isdir(target_path):
        # find all markdown files
        for root, dirs, files in os.walk(target_path):
            for f in files:
                if f.endswith('.md') or f.endswith('.txt'):
                    files_to_process.append(os.path.join(root, f))
    else:
        print(f"Lỗi: Không tìm thấy file hoặc thư mục '{target_path}'")
        sys.exit(1)

    print(f"Bắt đầu phân tích {len(files_to_process)} files...\n")

    all_chuong_lengths = []
    all_dieu_lengths = []

    for f_path in files_to_process:
        c_len, d_len = process_file(f_path)
        all_chuong_lengths.extend(c_len)
        all_dieu_lengths.extend(d_len)

    def print_stats(lengths, name):
        print(f"--- Thống kê TOÀN BỘ cho {name} ---")
        if not lengths:
            print(f"Không tìm thấy {name} nào.\n")
            return
            
        print(f"Tổng số lượng: {len(lengths)}")
        print(f"Min (ký tự): {min(lengths):,}")
        print(f"Max (ký tự): {max(lengths):,}")
        print(f"Average: {statistics.mean(lengths):,.2f}")
        print(f"Median: {statistics.median(lengths):,.2f}")
        print(f"Std Dev: {statistics.stdev(lengths) if len(lengths) > 1 else 0:,.2f}\n")

    print("=" * 40)
    print_stats(all_chuong_lengths, "CHƯƠNG")
    print_stats(all_dieu_lengths, "ĐIỀU")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Thống kê kích thước Chương và Điều trong văn bản luật.")
    parser.add_argument("path", help="Đường dẫn tới file hoặc thư mục chứa nội dung luật")
    args = parser.parse_args()
    
    analyze_law_structure(args.path)
