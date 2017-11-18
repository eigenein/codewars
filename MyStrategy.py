from collections import defaultdict, deque
from itertools import product
from math import hypot, sqrt
from random import shuffle
from statistics import mean
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from model.ActionType import ActionType
from model.Game import Game
from model.Move import Move
from model.Player import Player
from model.Vehicle import Vehicle
from model.VehicleType import VehicleType
from model.VehicleUpdate import VehicleUpdate
from model.World import World


VEHICLE_TYPES = {VehicleType.ARRV, VehicleType.FIGHTER, VehicleType.HELICOPTER, VehicleType.IFV, VehicleType.TANK}
AERIAL_TYPES = {VehicleType.FIGHTER, VehicleType.HELICOPTER}
GROUND_TYPES = {VehicleType.ARRV, VehicleType.IFV, VehicleType.TANK}

Cluster = NamedTuple('Cluster', [('vehicles', List[Vehicle])])


class MyStrategy:
    def __init__(self):
        self.action_queue = deque()

        unit_tracker = UnitTracker(self)

        self.pre_trackers = (unit_tracker,)
        self.decision_makers = (
            InitialSetupDecisionMaker(self, unit_tracker),
            NuclearStrikeDecisionMaker(self, unit_tracker),
            MoveDecisionMaker(self, unit_tracker),
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
        if self.me.remaining_action_cooldown_ticks == 0:
            self.action_queue.popleft()()

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


class InitialSetupDecisionMaker:
    def __init__(self, strategy: MyStrategy, unit_tracker: 'UnitTracker'):
        self.strategy = strategy
        self.unit_tracker = unit_tracker

    def move(self) -> bool:
        if self.strategy.world.tick_index != 0:
            return False
        for vehicle_type in VEHICLE_TYPES:
            self.make_groups(vehicle_type)
        return True

    def make_groups(self, vehicle_type: int):
        vehicles = [
            vehicle for vehicle in self.unit_tracker.vehicles.values()
            if vehicle.player_id == self.strategy.me.id and vehicle.type == vehicle_type
        ]
        x = mean(vehicle.x for vehicle in vehicles)
        y = mean(vehicle.y for vehicle in vehicles)
        self.strategy.select(vehicle_type=vehicle_type, right=x, bottom=y)
        self.strategy.assign(self.unit_tracker.allocate_group())
        self.strategy.select(vehicle_type=vehicle_type, left=x, bottom=y)
        self.strategy.assign(self.unit_tracker.allocate_group())
        self.strategy.select(vehicle_type=vehicle_type, right=x, top=y)
        self.strategy.assign(self.unit_tracker.allocate_group())
        self.strategy.select(vehicle_type=vehicle_type, left=x, top=y)
        self.strategy.assign(self.unit_tracker.allocate_group())


class NuclearStrikeDecisionMaker:
    def __init__(self, strategy: MyStrategy, unit_tracker: 'UnitTracker'):
        self.strategy = strategy
        self.unit_tracker = unit_tracker

    def move(self) -> bool:
        pass


class UnitTracker:
    CELL_COUNT = 64
    CELL_SIZE = 1024 / CELL_COUNT
    CELL_SIZE_SQUARED = CELL_SIZE * CELL_SIZE
    SCAN_RANGE = range(-1, 2)

    def __init__(self, strategy: MyStrategy):
        self.strategy = strategy
        self.vehicles = {}  # type: Dict[int, Vehicle]
        self.cells = defaultdict(dict)  # type: Dict[Tuple[int, int], Dict[int, Vehicle]]
        self.clusters = []  # type: List[Cluster]
        self.group_vehicles = {}
        self._groups = []  # type: List[int]

    def move(self):
        self.add_new_vehicles()
        self.update_vehicles()
        self.update_groups()
        if self.strategy.me.remaining_action_cooldown_ticks == 0:
            self.update_clusters()

    def allocate_group(self) -> int:
        group = len(self._groups) + 1
        self._groups.append(group)
        return group

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
        for update in self.strategy.world.vehicle_updates:  # type: VehicleUpdate
            vehicle = self.vehicles[update.id]
            # Get out the vehicle of the cell.
            self.get_cell(vehicle).pop(vehicle.id, None)
            # Pop if killed.
            if update.durability == 0:
                self.vehicles.pop(update.id, None)
                continue
            # Update attributes.
            vehicle.x = update.x
            vehicle.y = update.y
            vehicle.durability = update.durability
            vehicle.groups = update.groups
            vehicle.selected = update.selected
            vehicle.remaining_attack_cooldown_ticks = update.remaining_attack_cooldown_ticks
            # Put to the right cell.
            if vehicle.player_id == self.strategy.opponent_player_id:
                self.get_cell(vehicle)[vehicle.id] = vehicle

    def update_clusters(self):
        self.clusters = sorted(self.split_opponent_vehicles(), key=(lambda cluster: len(cluster.vehicles)), reverse=True)
        self.strategy.log_message('clusters: {}', [len(cluster.vehicles) for cluster in self.clusters])

    def update_groups(self):
        self.group_vehicles = defaultdict(list)
        for vehicle in self.vehicles.values():
            if vehicle.player_id == self.strategy.me.id:
                for group in vehicle.groups:
                    self.group_vehicles[group].append(vehicle)

    def split_opponent_vehicles(self) -> Iterable[Cluster]:
        """
        Split opponent vehicles into clusters.
        """
        cells = {cell for cell, vehicles in self.cells.items() if vehicles}
        while cells:
            vehicles = list(self.bfs(cells, *cells.pop()))
            if vehicles:
                yield Cluster(vehicles)

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

    def get_cell(self, vehicle: Vehicle) -> Dict[int, Vehicle]:
        return self.cells[int(vehicle.x // UnitTracker.CELL_SIZE), int(vehicle.y // UnitTracker.CELL_SIZE)]

    def get_group_center(self, group: int) -> 'Vector':
        return Vector(
            mean(vehicle.x for vehicle in self.group_vehicles[group]),
            mean(vehicle.y for vehicle in self.group_vehicles[group]),
        )

    def get_cluster_center(self, index: int) -> 'Vector':
        cluster = self.clusters[index]
        return Vector(
            mean(vehicle.x for vehicle in cluster.vehicles),
            mean(vehicle.y for vehicle in cluster.vehicles),
        )


class MoveDecisionMaker:
    def __init__(self, strategy: MyStrategy, unit_tracker: UnitTracker):
        self.strategy = strategy
        self.unit_tracker = unit_tracker

    def move(self):
        group_centers = {
            group: self.unit_tracker.get_group_center(group)
            for group in self.unit_tracker.group_vehicles
        }  # type: Dict[int, Vector]
        cluster_centers = {
            index: self.unit_tracker.get_cluster_center(index)
            for index, _ in enumerate(self.unit_tracker.clusters)
        }  # type: Dict[int, Vector]

        groups = list(self.unit_tracker.group_vehicles)
        shuffle(groups)

        group_forces = {}  # type: Dict[int, Vector]
        # Compute forces.
        for group in groups:
            center = group_centers[group]
            force = Vector.zero()
            for another_group, another_center in group_centers.items():
                if group == another_group:
                    continue
                # Repulsed by other groups.
                force -= 50.0 * (another_center - center).force
            for cluster_index, cluster_center in cluster_centers.items():
                # Attracted by clusters.
                force += len(self.unit_tracker.clusters[cluster_index].vehicles) * (cluster_center - center).force
            group_forces[group] = force
        # Perform movements.
        for group in groups:
            self.strategy.select_group(group)
            self.strategy.go((lambda force_=group_forces[group].unit: 32.0 * force_), max_speed=0.18)

        return True


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
    def length_squared(self) -> float:
        return hypot(self.x, self.y)

    @property
    def length(self) -> float:
        return sqrt(self.length_squared)

    @property
    def unit(self) -> 'Vector':
        return self / self.length

    @property
    def just_x(self) -> 'Vector':
        return Vector(self.x, 0.0)

    @property
    def just_y(self) -> 'Vector':
        return Vector(0.0, self.y)

    @property
    def force(self) -> 'Vector':
        length_squared = self.length_squared
        length = sqrt(length_squared)
        return self / length / length_squared
