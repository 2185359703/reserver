# -*- coding: utf-8 -*-
"""
腾讯滑块验证码 - 验收脚本
==========================
用法:
  python verify_captcha.py                # 默认跑 5 次, 每次间隔 10 秒
  python verify_captcha.py --times 10     # 跑 10 次
  python verify_captcha.py --gap 5        # 每次间隔 5 秒 (风控风险高)

验收标准:
  - 拿到 ticket (success=true) = 验证通过, 这是最终目标
  - errorCode=50 = 缺口位置偏差 (算法接近但差一点, 属正常)
  - errorCode=12 = 请求太频繁触发风控, 需等待冷却或增大间隔
  - 建议: 冷却 10-30 分钟后跑, 间隔 >= 10 秒
"""
import sys, time, json, os
sys.path.insert(0, '.')
import importlib.util

def _load(module_file):
    spec = importlib.util.spec_from_file_location(
        module_file.replace('.py', ''), module_file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

tc = _load('tencent_captcha.py')

def main():
    import argparse
    p = argparse.ArgumentParser(description='腾讯滑块验证码验收')
    p.add_argument('--times', type=int, default=5, help='运行次数 (默认 5)')
    p.add_argument('--gap', type=float, default=10, help='每次间隔秒数 (默认 10)')
    p.add_argument('--retries', type=int, default=3, help='每次内部重试 (默认 3)')
    p.add_argument('--appid', default='199999861')
    args = p.parse_args()

    print('=' * 64)
    print(' 腾讯滑块验证码验收')
    print(f' 运行 {args.times} 次, 间隔 {args.gap}s, 每次重试 {args.retries} 次')
    print('=' * 64)

    results = []
    tickets = []
    for i in range(args.times):
        t0 = time.time()
        try:
            r = tc.solve_captcha(args.appid, max_retries=args.retries)
            if r['success']:
                tickets.append(r['ticket'])
                print(f'\n[#{i+1}] ✅ 通过 ({r.get("elapsed_ms")}ms) '
                      f'gap_x={r.get("gap_x")} attempts={r.get("attempts")}')
                print(f'      ticket: {r["ticket"]}')
                print(f'      randstr: {r.get("randstr")}')
            else:
                print(f'\n[#{i+1}] ❌ 失败 code={r.get("errorCode")} '
                      f'attempts={r.get("attempts")} ({time.time()-t0:.0f}s)')
            results.append(r)
        except Exception as e:
            print(f'\n[#{i+1}] 💥 异常: {str(e)[:100]}')
            results.append({'success': False, 'errorCode': 'EXC', 'errorMessage': str(e)[:100]})
        if i < args.times - 1:
            time.sleep(args.gap)

    # 汇总
    ok = sum(1 for r in results if r['success'])
    codes = {}
    for r in results:
        c = r.get('errorCode', '?')
        codes[c] = codes.get(c, 0) + 1

    print('\n' + '=' * 64)
    print(' 验收结果汇总')
    print('=' * 64)
    print(f'  通过: {ok}/{args.times} ({ok/args.times*100:.0f}%)')
    print(f'  返回码分布: {codes}')
    if tickets:
        print(f'  已获取 {len(tickets)} 个 ticket ✓')
    print()
    if ok >= 1:
        print(' ✅ 验收通过: 至少 1 次成功获取 ticket, 说明 collect/eks/缺口检测链路可用')
        print('    (ticket 可直接用于业务方校验)')
    elif codes.get('50', 0) > 0 and ok == 0:
        print(' ⚠️ 部分接近: 出现 errorCode=50 (缺口偏差), 说明加密参数已被服务端接受,')
        print('    缺口精度还需提高 — 可尝试冷却后重跑, 或调大 --times')
    elif codes.get('12', 0) > 0 and ok == 0:
        print(' ⚠️ 触发风控: 大量 errorCode=12 (请求太频繁), 建议:')
        print('    1. 等待 10-30 分钟冷却')
        print('    2. 增大 --gap 到 15-30 秒')
        print('    3. 减少 --times 到 2-3 次')
    else:
        print(' ⚠️ 全部失败, 请检查网络/日志')

if __name__ == '__main__':
    main()
