import json
import os
import math

def calculate_gpu_requirements(model_path, mem_per_gpu_gb):
    """
    计算逻辑：
    1. 静态权重: model_weight_size_gb
    2. 动态缓存: 推理时产生的 KV Cache 和激活值预留。
    3. 综合系数 (1.8): 
       - 1.0 (基础权重) + 0.5 (动态负载) + 0.3 (硬件驱动与算子库开销)。
    """
    index_path = os.path.join(model_path, 'model.safetensors.index.json')
    
    if not os.path.exists(index_path):
        total_weight_size_gb = 14.0 # 默认预设
    else:
        with open(index_path, 'r') as f:
            meta = json.load(f)
            total_weight_size_gb = meta["metadata"]["total_size"] / 1e9

    # 总显存需求预估
    estimated_vram_needed = total_weight_size_gb * 1.8
    
    # 计算卡数并确保并行度平衡
    gpu_count = math.ceil(estimated_vram_needed / mem_per_gpu_gb)
    
    # 针对国产加速卡优化：若多于 1 卡，优先凑成偶数以适配主流分布式拓扑
    if gpu_count > 1 and gpu_count % 2 != 0:
        gpu_count += 1
        
    return int(gpu_count)