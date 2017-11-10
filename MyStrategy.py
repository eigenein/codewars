from collections import deque
from math import pi
from statistics import StatisticsError, mean
from typing import Callable, Dict, Optional, Tuple

from model.ActionType import ActionType
from model.Game import Game
from model.Move import Move
from model.Player import Player
from model.Vehicle import Vehicle
from model.VehicleType import VehicleType
from model.VehicleUpdate import VehicleUpdate
from model.World import World


ACTION_NAME = {
    None: 'None',
    0: 'NONE',
    1: 'CLEAR_AND_SELECT',
    2: 'ADD_TO_SELECTION',
    3: 'DESELECT',
    4: 'ASSIGN',
    5: 'DISMISS',
    6: 'DISBAND',
    7: 'MOVE',
    8: 'ROTATE',
    9: 'SCALE',
    10: 'SETUP_VEHICLE_PRODUCTION',
}
MAX_SPEED = 0.3 * 0.6
AERIAL_TYPES = (VehicleType.FIGHTER, VehicleType.HELICOPTER)
GROUND_TYPES = (VehicleType.TANK, VehicleType.IFV, VehicleType.ARRV)


class MyStrategy:
    def __init__(self):
        self.action_queue = deque()
        self.vehicles = {}  # type: Dict[int, Vehicle]
        self.my_vehicles = {}  # type: Dict[int, Vehicle]
        self.enemy_vehicles = {}  # type: Dict[int, Vehicle]
        self.attack_matrix = {}  # type: Dict[Tuple[int, int], Optional[float]]
        self.freeze_ticks = 0
        self.shrink_count = 0
        self.next_action = 'ROTATE'

        self.me = None  # type: Player
        self.world = None  # type: World
        self.game = None  # type: Game

        self.my_x = 0.0
        self.my_y = 0.0

    def put_attack_range(self, attacker_type: int, ground_attack_range: Optional[float], aerial_attack_range: Optional[float]):
        for type_ in GROUND_TYPES:
            self.attack_matrix[attacker_type, type_] = ground_attack_range
        for type_ in AERIAL_TYPES:
            self.attack_matrix[attacker_type, type_] = aerial_attack_range

    # noinspection PyMethodMayBeStatic
    def move(self, me: Player, world: World, game: Game, move: Move):
        self.me = me
        self.world = world
        self.game = game

        if world.tick_index == 0:
            self.put_attack_range(VehicleType.TANK, game.tank_ground_attack_range, game.tank_aerial_attack_range)
            self.put_attack_range(VehicleType.FIGHTER, None, game.fighter_aerial_attack_range)
            self.put_attack_range(VehicleType.HELICOPTER, game.helicopter_ground_attack_range, game.helicopter_aerial_attack_range)
            self.put_attack_range(VehicleType.IFV, game.ifv_ground_attack_range, game.ifv_aerial_attack_range)

        # Update units.
        for vehicle in world.new_vehicles:  # type: Vehicle
            self.vehicles[vehicle.id] = vehicle
            if vehicle.player_id == me.id:
                self.my_vehicles[vehicle.id] = vehicle
            else:
                self.enemy_vehicles[vehicle.id] = vehicle
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
                self.my_vehicles.pop(update.id, None)
                self.enemy_vehicles.pop(update.id, None)

        # Update freeze.
        if self.freeze_ticks != 0:
            self.freeze_ticks -= 1

        try:
            self.my_x, self.my_y = self.get_my_center()
        except StatisticsError:
            pass

        # Check if something has to be done.
        if self.action_queue:
            if me.remaining_action_cooldown_ticks == 0 and self.freeze_ticks == 0:
                self.action_queue.popleft()(move)
                print('[{}] {}({:.2f}, {:.2f})'.format(self.world.tick_index, ACTION_NAME[move.action], move.x, move.y))
            return

        print('[{}] Next action: {}'.format(world.tick_index, self.next_action))
        if self.next_action == 'ROTATE':
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.TANK))
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.IFV, add_to_selection=True))
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.ARRV, add_to_selection=True))
            self.schedule(self.rotate_selected)
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.FIGHTER))
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.HELICOPTER, add_to_selection=True))
            self.schedule(self.rotate_selected)
            self.schedule(lambda _: self.reset_freeze(100))
            self.next_action = 'SHRINK'
        elif self.next_action == 'SHRINK':
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.TANK))
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.IFV, add_to_selection=True))
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.ARRV, add_to_selection=True))
            self.schedule(self.shrink_selected)
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.FIGHTER))
            self.schedule(lambda move_: self.select_all(move_, vehicle_type=VehicleType.HELICOPTER, add_to_selection=True))
            self.schedule(self.shrink_selected)
            self.schedule(lambda _: self.reset_freeze(50))
            self.shrink_count += 1
            self.next_action = 'MOVE' if self.shrink_count > 5 else 'ROTATE'
        elif self.next_action == 'MOVE':
            self.schedule(self.select_all)
            self.schedule(self.move_forward)
            self.schedule(lambda _: self.reset_freeze(50))
            density = self.get_density()
            print("[{}] Density: {:.3f}".format(world.tick_index, density))
            self.next_action = 'ROTATE' if density < 0.04 else 'MOVE'

    def schedule(self, action: Callable[[Move], None]):
        self.action_queue.append(action)

    def reset_freeze(self, freeze_ticks: int):
        self.freeze_ticks = freeze_ticks

    def get_my_center(self):
        return (
            mean(vehicle.x for vehicle in self.my_vehicles.values()),
            mean(vehicle.y for vehicle in self.my_vehicles.values()),
        )

    def get_selected_center(self):
        return (
            mean(vehicle.x for vehicle in self.my_vehicles.values() if vehicle.selected),
            mean(vehicle.y for vehicle in self.my_vehicles.values() if vehicle.selected),
        )

    def select_all(self, move: Move, vehicle_type=None, add_to_selection=False):
        move.action = ActionType.CLEAR_AND_SELECT if not add_to_selection else ActionType.ADD_TO_SELECTION
        move.left = 0.0
        move.top = 0.0
        move.right = self.game.world_width
        move.bottom = self.game.world_height
        if vehicle_type is not None:
            move.vehicle_type = vehicle_type

    def move_selected_to(self, move: Move, x: float, y: float):
        try:
            selected_x, selected_y = self.get_selected_center()
        except StatisticsError:
            return
        else:
            move.action = ActionType.MOVE
            move.max_speed = MAX_SPEED
            move.x = x - selected_x
            move.y = y - selected_y

    def move_forward(self, move: Move):
        try:
            enemy_vehicle = min(
                (vehicle for vehicle in self.enemy_vehicles.values()),
                key=(lambda vehicle: vehicle.get_distance_to(self.my_x, self.my_y)),
            )
        except ValueError:
            self.move_selected_to(move, self.world.width, self.world.height)
        else:
            self.move_selected_to(move, enemy_vehicle.x, enemy_vehicle.y)

    def rotate_selected(self, move: Move):
        move.x, move.y = self.my_x, self.my_y
        move.action = ActionType.ROTATE
        move.angle = pi

    def shrink_selected(self, move: Move):
        move.x, move.y = self.my_x, self.my_y
        move.action = ActionType.SCALE
        move.factor = 0.1

    def get_density(self):
        my_x, my_y = self.get_my_center()
        return len(self.my_vehicles.values()) / pi / max(vehicle.get_squared_distance_to(my_x, my_y) for vehicle in self.my_vehicles.values())
