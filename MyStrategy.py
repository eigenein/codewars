from collections import defaultdict, deque
from itertools import product
from math import hypot
from random import choice
from statistics import mean
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from model.ActionType import ActionType
from model.Game import Game
from model.Move import Move
from model.Player import Player
from model.TerrainType import TerrainType
from model.Vehicle import Vehicle
from model.VehicleType import VehicleType
from model.VehicleUpdate import VehicleUpdate
from model.WeatherType import WeatherType
from model.World import World


VEHICLE_TYPES = {VehicleType.ARRV, VehicleType.FIGHTER, VehicleType.HELICOPTER, VehicleType.IFV, VehicleType.TANK}
AERIAL_TYPES = {VehicleType.FIGHTER, VehicleType.HELICOPTER}
GROUND_TYPES = {VehicleType.ARRV, VehicleType.IFV, VehicleType.TANK}


Cluster = NamedTuple('Cluster', [('vehicles', List[Vehicle]), ('x', float), ('y', float)])


class MyStrategy:
    def __init__(self):
        self.action_queue = deque()
        self.next_action_tick = 0

        unit_tracker = UnitTracker(self)

        self.pre_trackers = (unit_tracker,)
        self.decision_makers = (
            NuclearStrikeDecisionMaker(self, unit_tracker),
            SpreadOutDecisionMaker(self, unit_tracker),
        )

        self.me = None  # type: Player
        self.world = None  # type: World
        self.game = None  # type: Game
        self.move_ = None  # type: Move
        self.opponent_player_id = None  # type: int

    def move(self, me: Player, world: World, game: Game, move: Move):
        """
        Entry point.
        """
        self.me = me
        self.world = world
        self.game = game
        self.move_ = move
        self.opponent_player_id = world.get_opponent_player().id

        for tracker in self.pre_trackers:
            tracker.move()
        if self.action_queue:
            self.process_action_queue()
        else:
            self.log_message('---------------------------------------------------------')
            self.make_decisions()

    def schedule_action(self, action: Callable[[], None]):
        """
        Put the action to the queue.
        """
        self.action_queue.append(action)

    def process_action_queue(self):
        """
        Process the next action if possible.
        """
        if self.next_action_tick <= self.world.tick_index:
            assert self.me.remaining_action_cooldown_ticks == 0, self.me.remaining_action_cooldown_ticks
            self.action_queue.popleft()()
            self.next_action_tick = self.world.tick_index + 5

    def log_message(self, message: str, *args, **kwargs):
        print('[{}] {}'.format(self.world.tick_index, message.format(*args, **kwargs)))

    def make_decisions(self):
        """
        This is where strategy decisions are made.
        """
        for decision_maker in self.decision_makers:
            if decision_maker.move():
                break

    def select(
        self,
        vehicle_type: Optional[int] = None,
        left: Optional[float] = None,
        top: Optional[float] = None,
        right: Optional[float] = None,
        bottom: Optional[float] = None,
    ):
        def wrapper():
            self.log_message('select {} ({}, {})-({}, {})', vehicle_type, left, top, right, bottom)
            self.move_.action = ActionType.CLEAR_AND_SELECT
            self.move_.left = 0.0 if left is None else left
            self.move_.top = 0.0 if top is None else top
            self.move_.right = self.game.world_width if right is None else right
            self.move_.bottom = self.game.world_height if bottom is None else bottom
            if vehicle_type is not None:
                self.move_.vehicle_type = vehicle_type
        self.schedule_action(wrapper)

    def select_vehicle(self, vehicle: Vehicle, radius: Optional[float] = None):
        radius = radius or vehicle.radius
        self.select(
            vehicle_type=vehicle.type,
            left=(vehicle.x - radius),
            top=(vehicle.y - radius),
            right=(vehicle.x + radius),
            bottom=(vehicle.y + radius),
        )

    def scale(self, get_center: Callable[[], 'Vector'], factor: float):
        def wrapper():
            self.move_.action = ActionType.SCALE
            self.move_.x, self.move_.y = get_center()
            self.move_.factor = factor
            self.log_message('scale around ({}, {}) by {}', self.move_.x, self.move_.y, factor)
        self.schedule_action(wrapper)

    def go(self, get_offset: Callable[[], 'Vector'], max_speed: Optional[float] = None):
        def wrapper():
            self.move_.action = ActionType.MOVE
            self.move_.x, self.move_.y = get_offset()
            if max_speed is not None:
                self.move_.max_speed = max_speed
            self.log_message('move by ({}, {}) speed {}', self.move_.x, self.move_.y, max_speed)
        self.schedule_action(wrapper)

    def assign(self, group: int):
        def wrapper():
            self.log_message('assign {}', group)
            self.move_.action = ActionType.ASSIGN
            self.move_.group = group
        self.schedule_action(wrapper)

    def select_group(self, group: int):
        def wrapper():
            self.log_message('select {}', group)
            self.move_.action = ActionType.CLEAR_AND_SELECT
            self.move_.group = group
        self.schedule_action(wrapper)

    def nuclear_strike(self, vehicle_id: int, point: 'Vector'):
        def wrapper():
            self.log_message('NUCLEAR STRIKE ({}, {})', point.x, point.y)
            self.move_.action = ActionType.TACTICAL_NUCLEAR_STRIKE
            self.move_.x = point.x
            self.move_.y = point.y
            self.move_.vehicle_id = vehicle_id
        self.schedule_action(wrapper)


class SpreadOutDecisionMaker:
    SQUARE_SIZE = 128.0

    def __init__(self, strategy: MyStrategy, unit_tracker: 'UnitTracker'):
        self.strategy = strategy
        self.unit_tracker = unit_tracker

    def move(self) -> bool:
        vehicle = choice(list(self.unit_tracker.my_vehicles))
        x = vehicle.x
        y = vehicle.y
        if x < 512:
            left = x
            right = x + self.SQUARE_SIZE
        else:
            right = x
            left = x - self.SQUARE_SIZE
        if y < 512:
            top = y
            bottom = y + self.SQUARE_SIZE
        else:
            top = y - self.SQUARE_SIZE
            bottom = y
        self.strategy.select(left=left, right=right, top=top, bottom=bottom)
        self.strategy.scale(lambda: Vector(x, y), 10.0)
        return True


class NuclearStrikeDecisionMaker:
    def __init__(self, strategy: MyStrategy, unit_tracker: 'UnitTracker'):
        self.strategy = strategy
        self.unit_tracker = unit_tracker
        self.terrain = None  # type: List[List[int]]
        self.weather = None  # type: List[List[int]]
        self.game = None  # type: Game

    def move(self) -> bool:
        if self.strategy.me.remaining_nuclear_strike_cooldown_ticks != 0:
            return False
        if not self.unit_tracker.clusters:
            return False

        self.game = self.strategy.game
        self.terrain = self.strategy.world.terrain_by_cell_x_y
        self.weather = self.strategy.world.weather_by_cell_x_y

        possible_strikes = []

        for vehicle in self.unit_tracker.my_vehicles:
            # Search for the closest cluster.
            cluster = min(self.unit_tracker.clusters, key=(lambda cluster: vehicle.get_squared_distance_to(cluster.x, cluster.y)))
            # Search for the closest vehicle in the cluster.
            opponent_vehicle = min(cluster.vehicles, key=(lambda another_vehicle: vehicle.get_squared_distance_to_unit(another_vehicle)))
            # Check if the opponent vehicle is reachable.
            if self.get_true_vehicle_vision_range(vehicle) > vehicle.get_distance_to_unit(opponent_vehicle):
                possible_strikes.append((vehicle, opponent_vehicle, cluster))

        if possible_strikes:
            # Search for a strike with the largest cluster and a vehicle closer to the cluster center.
            vehicle, opponent_vehicle, _ = max(
                possible_strikes,
                key=(lambda args: (len(args[2].vehicles), -args[1].get_squared_distance_to(args[2].x, args[2].y))),
            )
            self.strategy.select_vehicle(vehicle)
            self.strategy.go(Vector.zero)
            self.strategy.nuclear_strike(vehicle.id, Vector(opponent_vehicle.x, opponent_vehicle.y))
            return True

        return False

    def get_true_vehicle_vision_range(self, vehicle: Vehicle) -> float:
        i = int(vehicle.x // 32)
        j = int(vehicle.y // 32)
        if vehicle.type in GROUND_TYPES:
            terrain = self.terrain[i][j]
            if terrain == TerrainType.FOREST:
                return vehicle.vision_range * self.game.forest_terrain_vision_factor
            if terrain == TerrainType.PLAIN:
                return vehicle.vision_range * self.game.plain_terrain_vision_factor
            if terrain == TerrainType.SWAMP:
                return vehicle.vision_range * self.game.swamp_terrain_vision_factor
        else:
            weather = self.weather[i][j]
            if weather == WeatherType.CLEAR:
                return vehicle.vision_range * self.game.clear_weather_vision_factor
            if weather == WeatherType.CLOUD:
                return vehicle.vision_range * self.game.cloud_weather_vision_factor
            if weather == WeatherType.RAIN:
                return vehicle.vision_range * self.game.rain_weather_vision_factor
        return vehicle.vision_range  # unreachable


class UnitTracker:
    CELL_COUNT = 64
    CELL_SIZE = 1024 / CELL_COUNT
    CELL_SIZE_SQUARED = CELL_SIZE * CELL_SIZE
    SCAN_RANGE = range(-1, 2)
    DELTA = 0.001

    def __init__(self, strategy: MyStrategy):
        self.strategy = strategy
        self.vehicles = {}  # type: Dict[int, Vehicle]
        self.cells = defaultdict(dict)  # type: Dict[Tuple[int, int], Dict[int, Vehicle]]
        self.clusters = []  # type: List[Cluster]
        self.moving_vehicles = set()  # type: Set[int]

    def move(self):
        self.add_new_vehicles()
        self.update_vehicles()
        if not self.strategy.action_queue:
            self.update_clusters()

    @property
    def my_vehicles(self) -> Iterable[Vehicle]:
        return (vehicle for vehicle in self.vehicles.values() if vehicle.player_id == self.strategy.me.id)

    def add_new_vehicles(self):
        """
        Add new vehicles on each tick.
        """
        for vehicle in self.strategy.world.new_vehicles:  # type: Vehicle
            self.vehicles[vehicle.id] = vehicle
            if vehicle.player_id == self.strategy.opponent_player_id:
                self.get_cell(vehicle)[vehicle.id] = vehicle

    def update_vehicles(self):
        """
        Update vehicles on each tick.
        """
        self.moving_vehicles.clear()

        for update in self.strategy.world.vehicle_updates:  # type: VehicleUpdate
            vehicle = self.vehicles[update.id]
            self.get_cell(vehicle).pop(vehicle.id, None)
            if update.durability == 0:
                self.vehicles.pop(update.id, None)
                continue
            is_moving = abs(vehicle.x - update.x) > self.DELTA or abs(vehicle.y - update.y) > self.DELTA
            vehicle.x = update.x
            vehicle.y = update.y
            vehicle.durability = update.durability
            vehicle.groups = update.groups
            vehicle.selected = update.selected
            vehicle.remaining_attack_cooldown_ticks = update.remaining_attack_cooldown_ticks
            if is_moving:
                self.moving_vehicles.add(vehicle.id)
            if vehicle.player_id == self.strategy.opponent_player_id:
                self.get_cell(vehicle)[vehicle.id] = vehicle

    def update_clusters(self):
        self.clusters = sorted(self.split_opponent_vehicles(), key=(lambda cluster: len(cluster.vehicles)), reverse=True)
        self.strategy.log_message('clusters: {}', [len(cluster.vehicles) for cluster in self.clusters])

    def split_opponent_vehicles(self) -> Iterable[Cluster]:
        """
        Split opponent vehicles into clusters.
        """
        cells = {cell for cell, vehicles in self.cells.items() if vehicles}
        while cells:
            vehicles = list(self.bfs(cells, *cells.pop()))
            if vehicles:
                yield Cluster(vehicles, mean(vehicle.x for vehicle in vehicles), mean(vehicle.y for vehicle in vehicles))

    def bfs(self, cells: Set[Tuple[int, int]], i: int, j: int) -> Iterable[Vehicle]:
        """
        Run BFS from the specified cell.
        """
        queue = deque([(i, j)])
        while queue:
            i, j = queue.popleft()
            yield from self.cells[i, j].values()
            for delta_i, delta_j in product(self.SCAN_RANGE, self.SCAN_RANGE):
                next_i, next_j = i + delta_i, j + delta_j
                if (next_i, next_j) in cells and self.get_minimum_squared_distance(i, j, next_i, next_j) < self.CELL_SIZE_SQUARED:
                    cells.remove((next_i, next_j))
                    queue.append((next_i, next_j))

    def get_minimum_squared_distance(self, i1: int, j1: int, i2: int, j2: int) -> float:
        """
        Get minimum squared distance between vehicles in the clusters.
        """
        return min(
            vehicle_1.get_squared_distance_to_unit(vehicle_2)
            for vehicle_1, vehicle_2 in product(self.cells[i1, j1].values(), self.cells[i2, j2].values())
        )

    @staticmethod
    def get_cell_index(vehicle: Vehicle) -> Tuple[int, int]:
        return int(vehicle.x // UnitTracker.CELL_SIZE), int(vehicle.y // UnitTracker.CELL_SIZE)

    def get_cell(self, vehicle: Vehicle) -> Dict[int, Vehicle]:
        return self.cells[self.get_cell_index(vehicle)]


class Vector(NamedTuple('Vector', [('x', float), ('y', float)])):
    @staticmethod
    def zero() -> 'Vector':
        return Vector(0.0, 0.0)

    def __add__(self, other: 'Vector') -> 'Vector':
        return Vector(self.x + other.x, self.y + other.y)

    def __sub__(self, other: 'Vector') -> 'Vector':
        return Vector(self.x - other.x, self.y - other.y)

    def __mul__(self, other: float) -> 'Vector':
        return Vector(self.x * other, self.y * other)

    def __rmul__(self, other: float) -> 'Vector':
        return self * other

    def __truediv__(self, other: float) -> 'Vector':
        return Vector(self.x / other, self.y / other)

    def __neg__(self) -> 'Vector':
        return Vector(-self.x, -self.y)

    @property
    def length(self) -> float:
        return hypot(self.x, self.y)

    @property
    def unit(self) -> 'Vector':
        return self / self.length
