from collections import defaultdict, deque
from itertools import combinations, product
from operator import attrgetter
from statistics import StatisticsError, mean
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

Cluster = NamedTuple('Cluster', [('vehicles', List[Vehicle]), ('size', float)])


class MyStrategy:
    def __init__(self):
        self.action_queue = deque()

        unit_tracker = UnitTracker(self)

        self.trackers = (unit_tracker,)
        self.decision_makers = (
            InitialSetupDecisionMaker(self, unit_tracker),
            NuclearStrikeDecisionMaker(self, unit_tracker),
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

        for tracker in self.trackers:
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

    def select_all(self, vehicle_type: Optional[VehicleType] = None):
        def wrapper():
            self.log_message('select all {}', vehicle_type)
            self.move_.action = ActionType.CLEAR_AND_SELECT
            self.move_.left = 0.0
            self.move_.top = 0.0
            self.move_.right = self.game.world_width
            self.move_.bottom = self.game.world_height
            if vehicle_type is not None:
                self.move_.vehicle_type = vehicle_type
        self.schedule_action(wrapper)

    def scale(self, get_center: Callable[[], Tuple[float, float]], factor: float):
        def wrapper():
            self.move_.action = ActionType.SCALE
            self.move_.x, self.move_.y = get_center()
            self.move_.factor = factor
            self.log_message('scale around ({}, {}) by {}', self.move_.x, self.move_.y, factor)
        self.schedule_action(wrapper)

    def go(self, get_offset: Callable[[], Tuple[float, float]], max_speed: Optional[float] = None):
        def wrapper():
            self.move_.action = ActionType.MOVE
            self.move_.x, self.move_.y = get_offset()
            if max_speed is not None:
                self.move_.max_speed = max_speed
            self.log_message('move by ({}, {}) speed {}', self.move_.x, self.move_.y, max_speed)
        self.schedule_action(wrapper)


class InitialSetupDecisionMaker:
    UNIT_DIAMETER = 4.0

    def __init__(self, strategy: MyStrategy, unit_tracker: 'UnitTracker'):
        self.strategy = strategy
        self.unit_tracker = unit_tracker
        self.state = 0

    def move(self) -> bool:
        if self.state == -1:
            return False
        if self.unit_tracker.am_i_moving:
            return True
        if self.state == 0:
            self.strategy.select_all()
            self.strategy.go(lambda: (16.0, 16.0))
            self.set_state(1)
        elif self.state == 1:
            self.strategy.select_all()
            self.strategy.scale(self.unit_tracker.get_selected_center, 1.2)
            self.set_state(2)
        elif self.state == 2:
            for vehicle_type in VEHICLE_TYPES:
                self.strategy.select_all(vehicle_type)
                self.strategy.scale(self.unit_tracker.get_selected_center, 1.2)
            self.set_state(-1)
        return True

    def set_state(self, state: int):
        self.strategy.log_message('state {}', state)
        self.state = state


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
    DELTA = 0.001

    def __init__(self, strategy: MyStrategy):
        self.strategy = strategy
        self.vehicles = {}  # type: Dict[int, Vehicle]
        self.cells = defaultdict(dict)  # type: Dict[Tuple[int, int], Dict[int, Vehicle]]
        self.am_i_moving = False
        self.is_opponent_moving = False
        self._clusters = None  # type: List[Cluster]

    def move(self):
        self.reset()
        self.add_new_vehicles()
        self.update_vehicles()

    def reset(self):
        """
        Reset all fields that are normally not refreshed on each tick.
        """
        self._clusters = None

    @property
    def clusters(self) -> List[Cluster]:
        if self._clusters is None:
            self._clusters = sorted(self.clusterize_opponent_vehicles(), key=attrgetter('size'), reverse=True)
            self.strategy.log_message('clusters: {}', [(len(vehicles), size) for vehicles, size in self.clusters])
        return self._clusters

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
        self.am_i_moving = False
        self.is_opponent_moving = False

        for update in self.strategy.world.vehicle_updates:  # type: VehicleUpdate
            vehicle = self.vehicles[update.id]
            # Get out the vehicle of the cell.
            self.get_cell(vehicle).pop(vehicle.id, None)
            # Pop if killed.
            if update.durability == 0:
                self.vehicles.pop(update.id, None)
                continue
            # Update attributes.
            is_moving = abs(vehicle.x - update.x) > self.DELTA or abs(vehicle.y - update.y) > self.DELTA
            vehicle.x = update.x
            vehicle.y = update.y
            vehicle.durability = update.durability
            vehicle.groups = update.groups
            vehicle.selected = update.selected
            vehicle.remaining_attack_cooldown_ticks = update.remaining_attack_cooldown_ticks
            # Put to the right cell.
            if vehicle.player_id == self.strategy.opponent_player_id:
                self.get_cell(vehicle)[vehicle.id] = vehicle
                self.is_opponent_moving = self.is_opponent_moving or is_moving  # track movement
            else:
                self.am_i_moving = self.am_i_moving or is_moving  # track movement

    def clusterize_opponent_vehicles(self) -> Iterable[Cluster]:
        """
        Split opponent vehicles into clusters.
        """
        cells = {cell for cell, vehicles in self.cells.items() if vehicles}
        while cells:
            vehicles = list(self.bfs(cells, *cells.pop()))
            if vehicles:
                yield Cluster(vehicles, self.get_cluster_size(vehicles))

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
    def get_cluster_size(vehicles: Iterable[Vehicle]) -> float:
        """
        Get cluster size as the maximum distance between vehicles in the cluster.
        """
        return max((
            vehicle_1.get_distance_to_unit(vehicle_2)
            for vehicle_1, vehicle_2 in combinations(vehicles, 2)
        ), default=0.0)

    def get_cell(self, vehicle: Vehicle) -> Dict[int, Vehicle]:
        return self.cells[int(vehicle.x // UnitTracker.CELL_SIZE), int(vehicle.y // UnitTracker.CELL_SIZE)]

    def get_selected_center(self) -> Tuple[float, float]:
        try:
            return (
                mean(vehicle.x for vehicle in self.vehicles.values() if vehicle.selected),
                mean(vehicle.y for vehicle in self.vehicles.values() if vehicle.selected),
            )
        except StatisticsError:
            return 0.0, 0.0
