from collections import deque
from typing import Callable, Dict

from model.Game import Game
from model.Move import Move
from model.Player import Player
from model.Vehicle import Vehicle
from model.VehicleUpdate import VehicleUpdate
from model.World import World


class MyStrategy:
    def __init__(self):
        self.action_queue = deque()
        self.vehicles = {}  # type: Dict[int, Vehicle]

        self.me = None  # type: Player
        self.world = None  # type: World
        self.game = None  # type: Game
        self.move = None  # type: Move

    # noinspection PyMethodMayBeStatic
    def move(self, me: Player, world: World, game: Game, move: Move):
        self.me = me
        self.world = world
        self.game = game
        self.move = move

        self.add_new_vehicles()
        self.update_vehicles()
        self.process_action_queue()

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

    def schedule_action(self, action: Callable[[], None]):
        self.action_queue.append(action)

    def process_action_queue(self):
        if self.action_queue and self.me.remaining_action_cooldown_ticks == 0:
            self.action_queue.popleft()()

    def log_message(self, message: str, *args, **kwargs):
        print('[{}] {}'.format(self.world.tick_index, message.format(*args, **kwargs)))
