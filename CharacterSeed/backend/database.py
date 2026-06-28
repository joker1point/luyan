import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import event
from backend.config import settings

# 确保数据库目录存在（SQLite不会自动创建父目录）
db_path = settings.DATABASE_URL.replace("sqlite:///", "")
os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

# 创建数据库引擎
# [fix] 显式声明 utf-8 编码 + text_factory，避免历史数据因 latin-1 写入后乱码
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={
        "check_same_thread": False,  # SQLite需要这个配置
    }
)

# [fix] 确保 SQLite 连接使用 utf-8 文本工厂
# Python 3 的 sqlite3 默认 text_factory = str（utf-8），无需额外设置
# 注意：PRAGMA encoding 只对新建数据库有效，对已有数据库设置无效且可能干扰读取

# 创建SessionLocal类
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建Base类
Base = declarative_base()

# 依赖注入：获取数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """初始化数据库，创建所有表（测试和启动时调用）"""
    Base.metadata.create_all(bind=engine)
