from collections import deque
from math import pi, sqrt
from operator import itemgetter
from random import getrandbits
from statistics import mean
from typing import Callable, Dict, Iterable, Optional, Tuple

from model.ActionType import ActionType
from model.Game import Game
from model.Move import Move
from model.Player import Player
from model.Vehicle import Vehicle
from model.VehicleType import VehicleType
from model.VehicleUpdate import VehicleUpdate
from model.World import World


MAX_SPEED = 0.3 * 0.6
AERIAL_TYPES = (VehicleType.FIGHTER, VehicleType.HELICOPTER)
GROUND_TYPES = (VehicleType.TANK, VehicleType.IFV, VehicleType.ARRV)
ALL_TYPES = GROUND_TYPES + AERIAL_TYPES
CAN_ATTACK = {
    VehicleType.ARRV: set(),
    VehicleType.FIGHTER: set(AERIAL_TYPES),
    VehicleType.HELICOPTER: set(ALL_TYPES),
    VehicleType.IFV: set(ALL_TYPES),
    VehicleType.TANK: set(ALL_TYPES),
}


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
        self.r = 1024.0
        self.r2 = 1024.0 * 1024.0
        self.attack_ratio = 1.0

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

        # Pre-compute some useful values.
        self.my_x, self.my_y = self.get_my_center()
        self.r2 = max(vehicle.get_squared_distance_to(self.my_x, self.my_y) for vehicle in self.my_vehicles.values())
        self.r = sqrt(self.r2)

        my_attacker_count = self.get_attacker_count(self.my_vehicles.values(), self.enemy_vehicles.values())
        enemy_attacker_count = self.get_attacker_count(self.enemy_vehicles.values(), self.my_vehicles.values())
        self.attack_ratio = my_attacker_count / enemy_attacker_count if enemy_attacker_count != 0 else 1000000.0

        # Check if something has to be done.
        if self.action_queue:
            if me.remaining_action_cooldown_ticks == 0 and self.freeze_ticks == 0:
                self.action_queue.popleft()(move)
                print('[{}] {}({:.2f}, {:.2f})'.format(self.world.tick_index, move.action, move.x, move.y))
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
            self.next_action = 'ROTATE' if density < 0.039 else 'MOVE'

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

    def move_forward(self, move: Move):
        enemy_vehicle = min(
            (vehicle for vehicle in self.enemy_vehicles.values()),
            key=(lambda vehicle: vehicle.get_distance_to(self.my_x, self.my_y)),
        )
        x, y = enemy_vehicle.x, enemy_vehicle.y

        move.action = ActionType.MOVE
        move.max_speed = MAX_SPEED
        if self.me.score > self.world.get_opponent_player().score:
            # We're winning. Why take a risk? Slowly go away.
            move.x = -(x - self.my_x)
            move.y = -(y - self.my_y)
            move.max_speed = 0.01
        elif (
            self.attack_ratio >= 1.0 or
            enemy_vehicle.get_distance_to(self.my_x, self.my_y) > self.r + 20.0 or
            self.world.tick_index > 19000
        ):
            # We have enough vehicles or opponent is too far away, let's attack!
            move.x = x - self.my_x
            move.y = y - self.my_y
        elif self.me.remaining_nuclear_strike_cooldown_ticks == 0:
            # Let's try to change something with a nuclear strike.
            move.action = ActionType.TACTICAL_NUCLEAR_STRIKE
            move.x = enemy_vehicle.x
            move.y = enemy_vehicle.y
            move.vehicle_id = max((
                vehicle, distance
                for vehicle, distance in self.get_vehicles_with_distance_to(self.my_vehicles.values(), enemy_vehicle)
                if distance < vehicle.vision_range
            ), key=itemgetter(1))[0].id
        else:
            # We're losing the battle. Let's move left-right until something good happens.
            move.x = y - self.my_y
            move.y = -(x - self.my_x)
            if getrandbits(1):
                move.x = -move.x
                move.y = -move.y

    def rotate_selected(self, move: Move):
        if self.attack_ratio > 0.99:
            move.x, move.y = self.my_x, self.my_y
            move.action = ActionType.ROTATE
            move.angle = pi
        else:
            move.action = ActionType.NONE

    def shrink_selected(self, move: Move):
        move.x, move.y = self.my_x, self.my_y
        move.action = ActionType.SCALE
        move.factor = 0.1

    def get_density(self):
        return len(self.my_vehicles.values()) / pi / self.r2

    @staticmethod
    def get_attacker_count(attacker_vehicles: Iterable[Vehicle], attacked_vehicles: Iterable[Vehicle]):
        attacked_types = {vehicle.type for vehicle in attacked_vehicles}
        return sum(1 for vehicle in attacker_vehicles if CAN_ATTACK[vehicle.type] & attacked_types)

    @staticmethod
    def get_vehicles_with_distance_to(vehicles: Iterable[Vehicle], target: Vehicle) -> Iterable[Tuple[Vehicle, float]]:
        return (vehicle, vehicle.get_distance_to_unit(target) for vehicle in vehicles)
