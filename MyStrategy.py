from collections import deque
from functools import partialmethod, wraps
from typing import Any, Callable, Dict

from model.ActionType import ActionType
from model.Game import Game
from model.Move import Move
from model.Player import Player
from model.Vehicle import Vehicle
from model.VehicleUpdate import VehicleUpdate
from model.World import World


GROUP_ALL = 1


def action(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return partialmethod(func, *args, **kwargs)
    return wrapper


class MyStrategy:
    def __init__(self):
        self.action_queue = deque()
        self.vehicles = {}  # type: Dict[int, Vehicle]

        self.me = None  # type: Player
        self.world = None  # type: World
        self.game = None  # type: Game
        self.move = None  # type: Move

    def move(self, me: Player, world: World, game: Game, move: Move):
        self.me = me
        self.world = world
        self.game = game
        self.move = move

        self.add_new_vehicles()
        self.update_vehicles()

        if self.action_queue:
            self.process_action_queue()
        elif world.tick_index == 0:
            self.setup()
        else:
            self.make_decisions()

    def add_new_vehicles(self):
        for vehicle in self.world.new_vehicles:  # type: Vehicle
            self.vehicles[vehicle.id] = vehicle

    def update_vehicles(self):
        for update in self.world.vehicle_updates:  # type: VehicleUpdate
            if update.durability != 0:
                vehicle = self.vehicles[update.id]
                vehicle.x = update.x
                vehicle.y = update.y
                vehicle.durability = update.durability
                vehicle.groups = update.groups
                vehicle.selected = update.selected
                vehicle.remaining_attack_cooldown_ticks = update.remaining_attack_cooldown_ticks
            else:
                self.vehicles.pop(update.id, None)

    def schedule_action(self, action):
        self.action_queue.append(action)

    def process_action_queue(self):
        if self.me.remaining_action_cooldown_ticks == 0:
            self.action_queue.popleft()()

    def log_message(self, message: str, *args, **kwargs):
        print('[{}] {}'.format(self.world.tick_index, message.format(*args, **kwargs)))

    def setup(self):
        self.schedule_action(self.select_all())

    def make_decisions(self):
        pass

    @action
    def select_all(self):
        self.move.action = ActionType.CLEAR_AND_SELECT
        self.move.left = 0.0
        self.move.top = 0.0
        self.move.right = self.game.world_width
        self.move.bottom = self.game.world_height

    @action
    def assign_group(self, group: int):
        self.move.action = ActionType.ASSIGN
        self.move.group = group
