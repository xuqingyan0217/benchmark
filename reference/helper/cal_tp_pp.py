def get_parallel_strategy(gpu_count, config_json_path):
    """
    约束条件：
    - Tensor Parallel (TP) 必须能整除模型注意力头数 (num_attention_heads)。
    - 计算公式：TP * PP = GPU_COUNT。
    """
    with open(config_json_path, 'r') as f:
        config = json.load(f)
        heads = config.get("num_attention_heads", 0)
    
    best_tp = 1
    # 遍历合法 TP 值 (1, 2, 4, 8, 16)
    for tp_candidate in [1, 2, 4, 8, 16]:
        if tp_candidate <= gpu_count and gpu_count % tp_candidate == 0:
            if heads > 0 and heads % tp_candidate == 0:
                best_tp = tp_candidate
    
    best_pp = gpu_count // best_tp
    return best_tp, best_pp