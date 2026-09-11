"""
数据库连接层：创建数据库连接池并检查连接是否正常。
"""


from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.config import Settings

"""连接池创建函数：检查数据库地址并创建连接池。"""

def create_database_engine(settings: Settings) -> Engine:
    if settings.database_url is None:
        raise ValueError("请配置 TRAVELMIND_DATABASE_URL 后再检查数据库")

    try:
        # SecretStr 默认隐藏内容；只有交给数据库驱动时才显式取出真实地址。
        # make_url 解析协议、用户名、密码、主机、端口和数据库名，不执行网络请求。
        address = make_url(settings.database_url.get_secret_value())
    except ArgumentError:
        # 解析异常可能含原始地址；替换成安全说明，不把密码带到终端或日志。
        raise ValueError("TRAVELMIND_DATABASE_URL 不是有效的数据库连接地址") from None

    # +psycopg 明确选用本项目安装的 psycopg 3 驱动，避免误用其他驱动。
    if address.drivername != "postgresql+psycopg":
        raise ValueError("TRAVELMIND_DATABASE_URL 必须以 postgresql+psycopg:// 开头")

    return create_engine(
        address,
        pool_pre_ping=True,  # 借用旧连接前检查它是否存活，处理数据库重启后的失效连接。
        connect_args={"connect_timeout": 5},  # 单次连接建立最多等待5秒，避免无期限卡住。
        hide_parameters=True,  # SQL 执行异常和日志不显示绑定参数中的业务数据。
        echo=False,  # 不主动打印每条 SQL；也不要自行打印完整数据库地址。
    )


"""连接检查函数：查询当前数据库和用户，确认连接可用。"""

def check_connection(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        # text() 把 SQL 字符串包装成 SQLAlchemy 可以执行的对象。
        # 只查询当前数据库、用户名，不读业务表，也不返回密码。
        row = connection.execute(text("SELECT current_database(), current_user")).one()
        return {"database": str(row[0]), "user": str(row[1])}


