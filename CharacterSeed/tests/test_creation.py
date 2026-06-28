#!/usr/bin/env python3
"""
测试Creation Module的脚本
注意：需要先创建.env文件并填入DeepSeek API Key
"""
import sys
import os

# 添加backend到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from modules.creation import CreationModule
from crud import character as character_crud
from database import SessionLocal, init_db

def test_creation_pipeline():
    """测试Creation Pipeline"""
    print("=" * 60)
    print("测试 Creation Module")
    print("=" * 60)
    
    # 初始化数据库表
    init_db()
    print("✅ 数据库表初始化完成\n")
    
    # 测试输入
    test_inputs = [
        "落魄贵族少女，擅长剑术，性格坚毅",
        "一个快乐的酒馆老板，喜欢听客人讲故事"
    ]
    
    for i, user_input in enumerate(test_inputs, 1):
        print(f"\n测试 {i}: {user_input}")
        print("-" * 60)
        
        try:
            # 创建Creation Module
            module = CreationModule()
            
            # 运行Pipeline
            parsed_data, raw_response = module.run(user_input, input_type="text")
            
            # 打印结果
            print("✅ Pipeline执行成功！")
            print(f"角色名称: {parsed_data.get('name')}")
            print(f"世界设定: {parsed_data.get('world_setting', '')[:100]}...")
            print(f"人格属性: {parsed_data.get('personality')}")
            print(f"当前状态: {parsed_data.get('current_state')}")
            print(f"初始记忆数量: {len(parsed_data.get('initial_memories', []))}")
            
            # 保存到数据库
            db = SessionLocal()
            try:
                from crud.character import create_character
                import json
                
                db_character = create_character(
                    db=db,
                    name=parsed_data.get('name', f'测试角色{i}'),
                    description=user_input,
                    world_setting=parsed_data.get('world_setting'),
                    personality=json.dumps(parsed_data.get('personality', {}), ensure_ascii=False),
                    current_state=json.dumps(parsed_data.get('current_state', {}), ensure_ascii=False),
                    creation_raw=raw_response
                )
                print(f"\n✅ 角色已保存到数据库，ID: {db_character.id}")
            finally:
                db.close()
            
            print(f"\nLLM原始响应（前200字符）:\n{raw_response[:200]}...")
            
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)

if __name__ == "__main__":
    # 检查.env文件
    if not os.path.exists(".env"):
        print("❌ 错误：找不到.env文件")
        print("请先复制.env.example为.env，并填入你的DeepSeek API Key")
        print("命令: copy .env.example .env")
        sys.exit(1)
    
    test_creation_pipeline()
