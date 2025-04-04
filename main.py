import json
import os
from datetime import datetime
from typing import Dict, List
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

# === 全局配置 ===
TASKS_FILE = "tasks.json"
POINTS_FILE = "points.json"
ADMIN_IDS = "2195556927"  # 管理员用户ID（用逗号分隔）
TASK_PERMISSION_MODE = 0      # 发布任务选项 0=所有人可发布 1=仅管理员发布

# === 初始化处理 ===
admin_list = [uid.strip() for uid in ADMIN_IDS.split(",") if uid.strip()]

def load_data(file_path: str) -> List[Dict]:
    """加载JSON数据文件"""
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r") as f:
        return json.load(f)

def save_data(data: List[Dict], file_path: str):
    """保存数据到JSON文件"""
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

def migrate_old_data():
    """数据迁移：兼容旧版数据结构"""
    tasks = load_data(TASKS_FILE)
    updated = False
    
    for task in tasks:
        # 迁移旧版 accepted_by 字段
        if "accepted_by" in task and "accepted_by_id" not in task:
            task["accepted_by_id"] = task["accepted_by"]
            task["accepted_by_name"] = "历史用户"
            del task["accepted_by"]
            updated = True
        
        # 补充缺失的发布者名称字段
        if "publisher_name" not in task:
            task["publisher_name"] = "历史发布者"
            updated = True
            
    if updated:
        save_data(tasks, TASKS_FILE)

@register("task_system", "Developer", "任务管理系统", "3.2.1")
class AdvancedTaskSystem(Star):
    def __init__(self, context: Context):
        migrate_old_data()  # 初始化时执行数据迁移
        super().__init__(context)
        self.admin_ids = admin_list

    def _get_user_info(self, event: AstrMessageEvent) -> Dict:
        """安全获取用户信息"""
        return {
            "id": event.get_sender_id(),
            "name": event.get_sender_name() or "未知用户"
        }

    # === 任务发布模块 ===
    @filter.command("发布任务")
    async def create_task(self, event: AstrMessageEvent, *, content: str):
        """发布新任务"""
        user = self._get_user_info(event)
        if TASK_PERMISSION_MODE == 1 and user["id"] not in self.admin_ids:
            yield event.plain_result("❌ 仅管理员可发布任务")
            return

        tasks = load_data(TASKS_FILE)
        task_id = datetime.now().strftime("%y%m%d%H%M%S")
        
        new_task = {
            "task_id": task_id,
            "publisher_id": user["id"],
            "publisher_name": user["name"],
            "content": content,
            "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
            "accepted_by_id": None,
            "accepted_by_name": None
        }
        
        tasks.append(new_task)
        save_data(tasks, TASKS_FILE)
        
        yield event.plain_result(
            f"📌 新任务已创建\n"
            f"ID：{task_id}\n内容：{content}"
        )

    # === 任务接受模块 ===
    @filter.command("接受任务")
    async def accept_task(self, event: AstrMessageEvent, task_id: str):
        """接受可用任务"""
        user = self._get_user_info(event)
        tasks = load_data(TASKS_FILE)
        
        for task in tasks:
            if task["task_id"] == task_id and task["status"] == "pending":
                task.update({
                    "status": "accepted",
                    "accepted_by_id": user["id"],
                    "accepted_by_name": user["name"]
                })
                save_data(tasks, TASKS_FILE)
                yield event.plain_result(f"✅ 已接受任务 {task_id}")
                return
        yield event.plain_result("❌ 任务不可接受")

    # === 任务完成模块 ===
    @filter.command("完成任务")
    async def user_complete(self, event: AstrMessageEvent, task_id: str):
        """用户提交任务完成"""
        user = self._get_user_info(event)
        tasks = load_data(TASKS_FILE)
        
        for task in tasks:
            if task["task_id"] == task_id and task["status"] == "accepted":
                if task["accepted_by_id"] != user["id"]:
                    yield event.plain_result("❌ 这不是你的任务")
                    return
                
                task["status"] = "pending_review"
                save_data(tasks, TASKS_FILE)
                
                # 通知所有管理员
                admin_mentions = " ".join([f"@{uid}" for uid in self.admin_ids])
                yield event.plain_result(
                    f"📢 任务完成待审核\n"
                    f"任务ID：{task_id}\n"
                    f"执行人：{user['name']}\n"
                    f"{admin_mentions} 请及时审核"
                )
                return
        yield event.plain_result("❌ 无效任务ID")

    # === 任务审核模块 ===
    @filter.command("审核任务")
    async def review_task(self, event: AstrMessageEvent, task_id: str):
        """管理员审核任务"""
        user = self._get_user_info(event)
        if user["id"] not in self.admin_ids:
            yield event.plain_result("⛔ 需要管理员权限")
            return

        tasks = load_data(TASKS_FILE)
        points = load_data(POINTS_FILE)
        
        target_task = next(
            (t for t in tasks 
             if t["task_id"] == task_id 
             and t["status"] == "pending_review"), 
            None
        )
        
        if not target_task:
            yield event.plain_result("❌ 无效的任务ID")
            return
            
        # 更新任务状态
        target_task["status"] = "completed"
        
        # 更新积分
        completer_id = target_task["accepted_by_id"]
        user_points = next(
            (p for p in points if p["user_id"] == completer_id),
            {"user_id": completer_id, "name": target_task["accepted_by_name"], "points": 0}
        )
        user_points["points"] += 10
        
        if user_points not in points:
            points.append(user_points)
        
        save_data(tasks, TASKS_FILE)
        save_data(points, POINTS_FILE)
        
        # 发送双通知
        yield event.plain_result(
            f"🎉 任务审核通过通知\n"
            f"任务ID：{task_id}\n"
            f"@{target_task['publisher_name']} 您发布的任务已完成\n"
            f"@{target_task['accepted_by_name']} 获得10积分（当前：{user_points['points']}）"
        )

    # === 任务查询模块 ===
    @filter.command("我的任务")
    async def list_tasks(self, event: AstrMessageEvent):
        """查询用户相关任务"""
        user = self._get_user_info(event)
        tasks = load_data(TASKS_FILE)
        
        my_tasks = []
        for t in tasks:
            # 自己发布的任务
            if t["publisher_id"] == user["id"] and t["status"] != "completed":
                my_tasks.append({
                    "type": "我发布的",
                    "id": t["task_id"],
                    "status": t["status"],
                    "content": t["content"]
                })
            # 自己接受的任务
            if t.get("accepted_by_id") == user["id"] and t["status"] != "completed":
                my_tasks.append({
                    "type": "我接受的",
                    "id": t["task_id"],
                    "status": t["status"],
                    "content": t["content"]
                })
        
        if not my_tasks:
            yield event.plain_result("📭 没有找到相关任务")
            return
            
        response = ["📋 我的任务列表"]
        for task in my_tasks:
            status_map = {
                "pending": "待接受",
                "accepted": "进行中", 
                "pending_review": "待审核",
                "completed": "已完成"
            }
            response.append(
                f"{task['type']} - {status_map[task['status']]}\n"
                f"ID：{task['id']}\n"
                f"内容：{task['content']}\n"
                f"————————————"
            )
            
        yield event.plain_result("\n".join(response))

    # === 全局任务列表 ===
    @filter.command("任务列表")
    async def list_all_tasks(self, event: AstrMessageEvent):
        """查看全平台任务状态"""
        tasks = load_data(TASKS_FILE)
        
        if not tasks:
            yield event.plain_result("📭 当前没有进行中的任务")
            return

        task_groups = {
            "🟢 可接受任务（未认领）": [],
            "🟡 进行中任务（已接受未完成）": [],
            "🟠 待审核任务（等待验收）": [],
            "🔴 已完成任务（已通过审核）": []
        }
        
        for task in tasks:
            # 数据兼容处理
            publisher = task.get("publisher_name", "未知发布者")
            accepted = task.get("accepted_by_name", "暂无")
            content_preview = (task["content"][:20] + "...") if len(task["content"]) > 20 else task["content"]
            
            item = (
                f"ID：{task['task_id']}\n"
                f"内容：{content_preview}\n"
                f"发布者：{publisher}\n"
                f"状态：{self._get_status_label(task['status'])}"
            )
            
            if accepted != "暂无":
                item += f"\n执行者：{accepted}"
                
            # 分类处理
            status = task.get("status", "pending")
            if status == "pending":
                task_groups["🟢 可接受任务（未认领）"].append(item)
            elif status == "accepted":
                task_groups["🟡 进行中任务（已接受未完成）"].append(item)
            elif status == "pending_review":
                task_groups["🟠 待审核任务（等待验收）"].append(item)
            elif status == "completed":
                task_groups["🔴 已完成任务（已通过审核）"].append(item)

        response = ["📜 全平台任务列表"]
        for group_name, group_tasks in task_groups.items():
            if group_tasks:
                response.append(f"\n{group_name}（共{len(group_tasks)}个）")
                response.extend([f"▫️ {t}" for t in group_tasks])

        yield event.plain_result("\n".join(response))

    def _get_status_label(self, status: str) -> str:
        """获取状态标签"""
        status_labels = {
            "pending": "待接受",
            "accepted": "进行中",
            "pending_review": "待审核",
            "completed": "已完成"
        }
        return status_labels.get(status, "未知状态")

    # === 积分系统 ===
    @filter.command("我的积分")
    async def check_points(self, event: AstrMessageEvent):
        """查询用户积分"""
        user = self._get_user_info(event)
        points_data = load_data(POINTS_FILE)
        user_points = next(
            (p for p in points_data if p["user_id"] == user["id"]),
            {"points": 0}
        )
        yield event.plain_result(f"🏅 当前积分：{user_points['points']}")

    @filter.command("积分榜")
    async def points_rank(self, event: AstrMessageEvent):
        """显示积分排行榜"""
        points_data = load_data(POINTS_FILE)
        sorted_points = sorted(points_data, 
                             key=lambda x: x.get("points", 0), 
                             reverse=True)[:10]
        
        if not sorted_points:
            yield event.plain_result("📊 积分榜暂无数据")
            return
            
        rank_list = []
        for idx, p in enumerate(sorted_points, 1):
            display_name = p.get("name", f"用户{p['user_id'][:6]}")
            rank_list.append(f"{idx}. {display_name} - {p.get('points',0)}分")
            
        yield event.plain_result(
            "🏆 积分排行榜TOP10：\n" + "\n".join(rank_list)
        )
