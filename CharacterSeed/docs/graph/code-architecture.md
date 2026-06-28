# CharacterSeed 代码架构关系图

> 基于 CodeGraph 分析：194 文件，3,463 节点，7,365 条边

## 系统概览

```mermaid
graph TB
    subgraph Frontend["Frontend — React + Vite (web/react-vite/)"]
        direction TB
        P[Pages 12个]
        H[Hooks & Context]
        A[API Layer]
        C[Components 17个]
        
        P --> H
        H --> A
        A --> C
    end
    
    subgraph Backend["Backend — FastAPI (backend/)"]
        direction TB
        R[Routers 13个]
        S[Core Services]
        M[Modules 11个]
        E[Engines 子系统]
        D[Data Layer]
        
        R --> S
        S --> M
        M --> E
        E --> D
    end
    
    subgraph External["External Systems"]
        Q[Qwen 通义千问<br/>实时对话]
        AG[Agnes AI<br/>异步任务]
        DB[(SQLite<br/>character_seed.db)]
        ENV[.env<br/>API Keys]
    end
    
    Frontend -->|REST / SSE| Backend
    S -->|chat/stream| Q
    S -->|creation/growth/event| AG
    D --> DB
    S --> ENV
```

## 前端架构

```mermaid
graph LR
    subgraph Pages
        ChatPage
        CreatePage
        EventsPage
        GrowthPage
        MemoryPage
        WorldPage
        SettingsPage
        StatusPage
        CharacterDetailPage
        JiwenPage
        LogsPage
    end
    
    subgraph Hooks
        useCharacters
        useSessions
        useKeyboard
        useLocalStorage
        useToast
    end
    
    subgraph Context
        ApiProvider
        CharactersProvider
    end
    
    subgraph API
        api.js
        realApi.js
    end
    
    Pages --> Hooks
    Hooks --> Context
    Context --> API
    API -->|HTTP| Backend
```

## 后端架构

```mermaid
graph TB
    subgraph Routers
        chat_router
        character_router
        character_memory_router
        event_router
        growth_router
        memory_router
        jiwen_router
        world_router
        session_router
        llm_router
        logs_router
        performance_router
    end
    
    subgraph Services
        llm_service[llm_service 单例]
        logging_service
        chat_session_crud
        llm_settings_store
        db_migration
    end
    
    subgraph Modules
        creation
        interaction
        enhanced_interaction
        event_mod
        growth_mod
        time_mod
        memory_decay
        memory_extractor
        post_chat
        summary_trigger
    end
    
    subgraph Engines
        subgraph Jiwen
            jiwen_core
            jiwen_manager
            jiwen_scheduler
        end
        subgraph Memory
            short_term
            long_term
            context_manager
            knowledge_base
        end
        subgraph World
            world_engine
            location_tree
            relationship_network
            season_calendar
        end
    end
    
    subgraph Data
        database.py
        models.py
        schemas.py
        crud/
    end
    
    Routers --> Services
    Services --> Modules
    Modules --> Engines
    Engines --> Data
```

## Task Routing 规则

```mermaid
graph LR
    subgraph LLMService
        router{任务路由}
    end
    
    router -->|chat/chat_stream| Qwen[Qwen 通义千问<br/>TTFT 2-3s]
    router -->|creation/growth/event/<br/>time/memory_extraction/summary| Agnes[Agnes AI<br/>异步处理]
    
    style Qwen fill:#F4F3FF,stroke:#6941C6
    style Agnes fill:#F4F3FF,stroke:#6941C6
    style router fill:#FFF7ED,stroke:#C4320A
```

## 数据模型关系

```mermaid
erDiagram
    Character ||--o{ ChatSession : has
    ChatSession ||--o{ Message : contains
    Character ||--o{ Event : triggers
    Character ||--o{ GrowthLog : grows
    Character ||--o{ Memory : stores
    Character ||--o{ JiwenState : emotion
    Character ||--o{ Location : at
    Character ||--o{ Relationship : has
    
    Character {
        int id
        string name
        string soul_md
        string personality
        string background
    }
    
    ChatSession {
        int id
        int character_id
        string title
        datetime created_at
    }
    
    Message {
        int id
        int session_id
        string role
        string content
    }
    
    Event {
        int id
        int character_id
        string type
        string description
    }
    
    Memory {
        int id
        int character_id
        string content
        string type
    }
```

## 关键依赖关系

| 模块 | 依赖 |
|------|------|
| `chat_router` | `llm_service`, `chat_session_crud`, `post_chat` |
| `character_router` | `character.py` (crud), `world_engine` |
| `event_router` | `event.py` (crud), `event` (module) |
| `growth_router` | `growth.py` (crud), `growth` (module) |
| `memory_router` | `memory.py` (crud), `short_term`, `long_term` |
| `jiwen_router` | `jiwen_manager`, `jiwen_scheduler` |
| `world_router` | `world_engine`, `location_tree`, `relationship_network` |
| `llm_service` | `llm_settings_store`, `.env`, Qwen/Agnes API |
| `post_chat` | `llm_service`, `memory_extractor`, `event` (module) |

## 文件结构总览

```
luyan/CharacterSeed/
├── backend/
│   ├── api/              # 13 个 Router
│   ├── crud/             # 数据访问层
│   ├── jiwen/            # 情绪引擎
│   ├── memory/           # 记忆系统
│   ├── modules/          # 业务模块 (11个)
│   ├── prompts/          # LLM 提示词
│   ├── services/         # 核心服务
│   ├── world/            # 世界引擎
│   ├── config.py         # 配置
│   ├── database.py       # 数据库连接
│   ├── main.py           # FastAPI 入口
│   ├── models.py         # ORM 模型
│   └── schemas.py        # Pydantic Schema
├── web/react-vite/
│   ├── src/
│   │   ├── components/   # 17 个组件
│   │   ├── hooks/        # 自定义 Hooks
│   │   ├── pages/        # 12 个页面
│   │   ├── router/       # 路由配置
│   │   ├── utils/        # 工具函数
│   │   ├── ApiContext.jsx
│   │   ├── CharactersContext.jsx
│   │   └── App.jsx
│   └── vite.config.js
└── tests/                # 测试用例
```
