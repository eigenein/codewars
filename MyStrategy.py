from collections import deque
from math import pi
from statistics import StatisticsError, mean
from typing import Callable, Dict

from model.ActionType import ActionType
from model.Game import Game
from model.Move import Move
from model.Player import Player
from model.Vehicle import Vehicle
from model.VehicleUpdate import VehicleUpdate
from model.World import World


ACTION_NAME = {
    0: 'NONE',
    1: 'CLEAR_AND_SELECT',
    2: 'ADD_TO_SELECTION',
    3: 'DESELECT',
    4: 'ASSIGN',
    5: 'DISMISS',
    6: 'DISBAND',
    7: 'MOVE',
    8: 'ROTATE',
    9: 'SETUP_VEHICLE_PRODUCTION',
}
MAX_SPEED = 0.4 * 0.6


class MyStrategy:
    def __init__(self):
        self.action_queue = deque()
        self.vehicles = {}  # type: Dict[int, Vehicle]
        self.freeze_ticks = 0
        self.next_action = 'ROTATE'
        self.me = None  # type: Player
        self.world = None  # type: World
        self.game = None  # type: Game

    # noinspection PyMethodMayBeStatic
    def move(self, me: Player, world: World, game: Game, move: Move):
        self.me = me
        self.world = world
        self.game = game

        # Update units.
        for vehicle in world.new_vehicles:  # type: Vehicle
            self.vehicles[vehicle.id] = vehicle
        for update in world.vehicle_updates:  # type: VehicleUpdate
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

        # Update freeze.
        if self.freeze_ticks != 0:
            self.freeze_ticks -= 1

        # Check if something has to be done.
        if self.action_queue:
            if me.remaining_action_cooldown_ticks == 0 and self.freeze_ticks == 0:
                self.action_queue.popleft()(move)
                print('[{}] {}({:.2f}, {:.2f})'.format(self.world.tick_index, ACTION_NAME[move.action], move.x, move.y))
            return

        if self.next_action == 'ROTATE':
            self.schedule(self.select_all)
            self.schedule(self.rotate)
            self.next_action = 'SHRINK'
        elif self.next_action == 'SHRINK':
            for quadrant in (1, 2, 3, 4):
                self.schedule(lambda move_, quadrant_=quadrant: self.select_quadrant(move_, quadrant_))
                self.schedule(lambda move_, quadrant_=quadrant: self.shrink_selected(move_, quadrant_ == 4))
            self.next_action = 'MOVE'
        elif self.next_action == 'MOVE':
            self.schedule(self.select_all)
            self.schedule(self.move_forward)
            density = self.get_density()
            print("[{}] Density: {:.3f}".format(world.tick_index, density))
            self.next_action = 'ROTATE' if density < 0.03 else 'MOVE'

    def schedule(self, action: Callable[[Move], None]):
        self.action_queue.append(action)

    def reset_freeze(self):
        self.freeze_ticks = 50

    def get_my_center(self):
        return (
            mean(vehicle.x for vehicle in self.vehicles.values() if vehicle.player_id == self.me.id),
            mean(vehicle.y for vehicle in self.vehicles.values() if vehicle.player_id == self.me.id),
        )

    def get_selected_center(self):
        return (
            mean(vehicle.x for vehicle in self.vehicles.values() if vehicle.player_id == self.me.id and vehicle.selected),
            mean(vehicle.y for vehicle in self.vehicles.values() if vehicle.player_id == self.me.id and vehicle.selected),
        )

    def select_all(self, move: Move):
        move.action = ActionType.CLEAR_AND_SELECT
        move.left = 0.0
        move.top = 0.0
        move.right = self.game.world_width
        move.bottom = self.game.world_height

    def move_selected_to(self, move: Move, x: float, y: float):
        try:
            selected_x, selected_y = self.get_selected_center()
        except StatisticsError:
            return
        else:
            move.x = x - selected_x
            move.y = y - selected_y
            move.action = ActionType.MOVE
            move.max_speed = MAX_SPEED

    def move_forward(self, move: Move):
        self.reset_freeze()

        move.action = ActionType.MOVE
        move.max_speed = MAX_SPEED
        try:
            my_x, my_y = self.get_my_center()
            enemy_vehicle = min(
                (vehicle for vehicle in self.vehicles.values() if vehicle.player_id != self.me.id),
                key=(lambda vehicle: vehicle.get_distance_to(my_x, my_y)),
            )
        except (ValueError, StatisticsError):
            self.move_selected_to(move, self.world.width, self.world.height)
        else:
            self.move_selected_to(move, enemy_vehicle.x, enemy_vehicle.y)

    def rotate(self, move: Move):
        try:
            move.x, move.y = self.get_my_center()
        except StatisticsError:
            return
        else:
            move.action = ActionType.ROTATE
            move.angle = pi
            self.reset_freeze()

    def select_quadrant(self, move: Move, quadrant: int):
        try:
            my_x, my_y = self.get_my_center()
        except StatisticsError:
            return
        else:
            move.action = ActionType.CLEAR_AND_SELECT
            if quadrant in (1, 2):
                move.top = 0.0
                move.bottom = my_y
            if quadrant in (3, 4):
                move.top = my_y
                move.bottom = self.world.height
            if quadrant in (1, 4):
                move.right = self.world.width
                move.left = my_x
            if quadrant in (2, 3):
                move.left = 0.0
                move.right = my_x

    def shrink_selected(self, move: Move, reset_freeze: bool):
        try:
            my_x, my_y = self.get_my_center()
            self.move_selected_to(move, my_x, my_y)
        except StatisticsError:
            return
        else:
            if reset_freeze:
                self.reset_freeze()

    def get_density(self):
        my_vehicles = [vehicle for vehicle in self.vehicles.values() if vehicle.player_id == self.me.id]
        my_x, my_y = self.get_my_center()
        return len(my_vehicles) / pi / max(vehicle.get_squared_distance_to(my_x, my_y) for vehicle in my_vehicles)
