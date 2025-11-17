"""
Smart IoT Dashboard Demo - Showcasing @client_state + State Management

This demo demonstrates the full power of djust's state management system:
- @client_state: Cross-panel coordination via StateBus (NO JAVASCRIPT!)
- @optimistic: Instant device toggles
- @throttle: Simulated real-time sensor updates
- @debounce: Smooth slider and filter inputs
- @cache: Efficient data fetching

All 4 panels coordinate via client-side StateBus without any manual JavaScript!
"""

import random
from djust import LiveView
from djust.decorators import client_state, optimistic, debounce, throttle, cache
from djust_shared.views import BaseViewWithNavbar


class SmartDashboardView(BaseViewWithNavbar):
    """
    Smart Home/IoT Dashboard with multi-panel state coordination.

    Demonstrates how @client_state enables complex UIs with zero JavaScript.
    """
    template_name = 'demos/smart_dashboard.html'

    def mount(self, request, **kwargs):
        """Initialize dashboard state"""
        # Climate sensors (with decimal precision)
        self.temperature = 72.0
        self.humidity = 45.0
        self.target_temp = 72.0

        # Server-computed values (updated automatically)
        self.heat_index = self._calculate_heat_index(72.0, 45.0)
        self.comfort_level = self._calculate_comfort(72.0, 45.0)
        self.energy_cost = self._calculate_energy_cost(72.0)
        self.temperature_celsius = self._fahrenheit_to_celsius(72.0)
        self.heat_index_celsius = self._fahrenheit_to_celsius(self.heat_index)

        # Device management
        self.devices = self._init_devices()
        self.filter = "all"  # all, active, offline

        # Auto-simulation control
        self.auto_simulate = False

    def _init_devices(self):
        """Initialize smart home devices"""
        device_types = [
            ("💡", "Living Room Light"),
            ("🌡️", "Bedroom Thermostat"),
            ("🔌", "Kitchen Outlet"),
            ("📹", "Front Door Camera"),
            ("🔊", "Office Speaker"),
            ("💡", "Bedroom Light"),
            ("🚪", "Garage Door"),
            ("🌡️", "Living Room Thermostat"),
        ]

        return [
            {
                'id': i,
                'icon': icon,
                'name': name,
                'active': random.choice([True, True, True, False]),  # 75% active
                'room': name.split()[0],
            }
            for i, (icon, name) in enumerate(device_types)
        ]

    # ========================================================================
    # CLIMATE CONTROL - @client_state + @throttle + @optimistic
    # ========================================================================

    @client_state(keys=["temperature"])  # Outermost: publish to state bus
    @throttle(interval=0.1)              # Middle: limit to 10 updates/sec
    @optimistic                           # Innermost: immediate UI update
    def set_temperature(self, temperature: float = 72.0, **kwargs):
        """
        User drags temperature slider.

        Decorator order (outer to inner):
        1. @optimistic (innermost): UI updates INSTANTLY at 60 FPS
        2. @throttle: Limits server requests to 10/sec (prevents spam)
        3. @client_state (outermost): Publishes to StateBus every 0.1s

        Result: Smooth slider + REAL-TIME cross-panel updates while dragging!

        Why @throttle instead of @debounce?
        - @debounce: Other panels only update AFTER you stop dragging (delayed)
        - @throttle: Other panels update CONTINUOUSLY while dragging (real-time!)

        Server-side validation (stricter than client):
        - Client slider allows: 60-85°F (0.1° increments)
        - Server only accepts: 65-80°F
        - If user tries 62.5°F, @optimistic shows it, but server rejects → auto-reverts to 65.0°F!
        """
        temp = float(temperature)

        # Server-side validation: stricter limits than client slider (60-85)
        if temp < 65.0:
            self.temperature = 65.0  # Server minimum
        elif temp > 80.0:
            self.temperature = 80.0  # Server maximum
        else:
            self.temperature = round(temp, 1)  # Round to 1 decimal place

        self.target_temp = self.temperature

        # Server-side calculations (computed automatically!)
        self.heat_index = self._calculate_heat_index(self.temperature, self.humidity)
        self.comfort_level = self._calculate_comfort(self.temperature, self.humidity)
        self.energy_cost = self._calculate_energy_cost(self.temperature)
        self.temperature_celsius = self._fahrenheit_to_celsius(self.temperature)
        self.heat_index_celsius = self._fahrenheit_to_celsius(self.heat_index)

    @client_state(keys=["humidity"])  # Outermost: publish to state bus
    @throttle(interval=0.1)           # Middle: limit to 10 updates/sec
    @optimistic                        # Innermost: immediate UI update
    def set_humidity(self, humidity: float = 45.0, **kwargs):
        """
        User drags humidity slider.

        @throttle gives real-time updates across panels while dragging.

        Server-side validation (stricter than client):
        - Client slider allows: 20-80% (0.1% increments)
        - Server only accepts: 30-70%
        - If user tries 25.5%, @optimistic shows it, but server rejects → auto-reverts to 30.0%!
        """
        hum = float(humidity)

        # Server-side validation: stricter limits than client slider (20-80)
        if hum < 30.0:
            self.humidity = 30.0  # Server minimum
        elif hum > 70.0:
            self.humidity = 70.0  # Server maximum
        else:
            self.humidity = round(hum, 1)  # Round to 1 decimal place

        # Server-side calculations (recomputed with new humidity!)
        self.heat_index = self._calculate_heat_index(self.temperature, self.humidity)
        self.comfort_level = self._calculate_comfort(self.temperature, self.humidity)
        self.heat_index_celsius = self._fahrenheit_to_celsius(self.heat_index)

    # ========================================================================
    # SIMULATED SENSOR UPDATES - @throttle + @client_state
    # ========================================================================

    @client_state(keys=["temperature", "humidity"])
    @throttle(interval=2.0)
    def simulate_sensor(self, **kwargs):
        """
        Simulates IoT sensor updates (called via client-side interval).

        Flow:
        1. Client calls this every 2 seconds (throttled)
        2. Server adds random fluctuation
        3. Publishes to StateBus
        4. All panels update in real-time

        Demonstrates: Real-time dashboard updates with rate limiting
        """
        # Drift towards target temperature
        if self.temperature < self.target_temp:
            self.temperature = min(self.target_temp, self.temperature + random.randint(0, 2))
        elif self.temperature > self.target_temp:
            self.temperature = max(self.target_temp, self.temperature - random.randint(0, 2))
        else:
            # Small random fluctuation around target
            self.temperature = self.target_temp + random.randint(-1, 1)

        # Humidity fluctuates randomly
        self.humidity = max(20, min(80, self.humidity + random.randint(-3, 3)))

    # ========================================================================
    # DEVICE MANAGEMENT - @optimistic + @client_state
    # ========================================================================

    @client_state(keys=["devices"])
    @optimistic
    def toggle_device(self, device_id: int = None, **kwargs):
        """
        Toggle device on/off with optimistic update.

        Flow:
        1. Client toggles checkbox INSTANTLY (optimistic)
        2. Publishes "devices" to StateBus
        3. Stats panel receives update and recalculates counts
        4. Server validates in background
        5. If server rejects, client auto-reverts

        Demonstrates: Instant UX + server validation
        """
        if device_id is None:
            return

        device = next((d for d in self.devices if d['id'] == int(device_id)), None)
        if device:
            device['active'] = not device['active']

    # ========================================================================
    # FILTERING - @client_state (server-side filtering)
    # ========================================================================

    @client_state(keys=["filter"])
    def update_filter(self, filter: str = "all", **kwargs):
        """
        Update device filter (all/active/offline).

        Flow:
        1. User selects filter from dropdown
        2. Server filters device list based on selection
        3. Publishes "filter" to StateBus
        4. Device list panel receives update and re-renders
        5. Stats panel receives update and recalculates

        Note: No @optimistic here because filtering requires server-side logic.
        The dropdown selection changes immediately (browser default), but the
        filtered device list must wait for server to compute and send patch.
        """
        self.filter = filter

    # ========================================================================
    # DATA FETCHING - @cache + @debounce
    # ========================================================================

    @cache(ttl=60, key_params=["device_id"])
    @debounce(wait=0.5)
    def fetch_device_details(self, device_id: int = None, **kwargs):
        """
        Fetch device history/details (simulated).

        Flow:
        1. User clicks device
        2. Client checks cache (1 minute TTL)
        3. If cache hit: Returns instantly (< 1ms)
        4. If cache miss: Debounces for 500ms, then queries server
        5. Server response cached for future requests

        Demonstrates: Efficient data fetching with caching
        """
        if device_id is None:
            return

        # Simulated device details (would be database query in real app)
        device = next((d for d in self.devices if d['id'] == int(device_id)), None)
        if device:
            return {
                'device_id': device_id,
                'details': f"Details for {device['name']}",
                'history': [
                    {'time': '10:00 AM', 'status': 'active'},
                    {'time': '09:30 AM', 'status': 'offline'},
                    {'time': '09:00 AM', 'status': 'active'},
                ]
            }

    # ========================================================================
    # CONTEXT & COMPUTED PROPERTIES
    # ========================================================================

    def get_context_data(self, **kwargs):
        """Add computed stats to context"""
        context = super().get_context_data(**kwargs)

        # Filter devices based on current filter
        if self.filter == "active":
            filtered_devices = [d for d in self.devices if d['active']]
        elif self.filter == "offline":
            filtered_devices = [d for d in self.devices if not d['active']]
        else:  # all
            filtered_devices = self.devices

        # Compute stats
        active_count = sum(1 for d in self.devices if d['active'])
        offline_count = len(self.devices) - active_count

        context.update({
            'temperature': self.temperature,
            'humidity': self.humidity,
            'target_temp': self.target_temp,
            'heat_index': self.heat_index,
            'comfort_level': self.comfort_level,
            'energy_cost': self.energy_cost,
            'temperature_celsius': self.temperature_celsius,
            'heat_index_celsius': self.heat_index_celsius,
            'devices': self.devices,
            'filtered_devices': filtered_devices,
            'filter': self.filter,
            'active_count': active_count,
            'offline_count': offline_count,
            'total_count': len(self.devices),
            'auto_simulate': self.auto_simulate,
        })

        return context

    # ========================================================================
    # SERVER-SIDE CALCULATIONS
    # ========================================================================

    def _calculate_heat_index(self, temp: float, humidity: float) -> int:
        """
        Calculate "feels like" temperature using humidity-adjusted formula.

        Server-side computation that updates automatically via @client_state.
        Works across all temperatures to demonstrate server-side calculation.
        """
        # Simplified formula that works at all temperatures
        # High humidity makes it feel hotter, low humidity makes it feel cooler

        # Base feels-like adjustment from humidity
        # At 50% humidity: no adjustment
        # Above 50%: feels hotter
        # Below 50%: feels cooler
        humidity_factor = (humidity - 50) / 100.0

        # Temperature amplifies the humidity effect
        # At high temps, humidity matters more
        temp_amplifier = 1 + ((temp - 65) / 20.0)

        # Calculate adjustment
        adjustment = humidity_factor * temp_amplifier * 5

        feels_like = temp + adjustment
        return int(round(feels_like))

    def _calculate_comfort(self, temp: float, humidity: float) -> str:
        """
        Calculate comfort level based on temp and humidity.

        Business logic computed server-side and broadcast via @client_state.
        """
        # Comfort zone: 68-76°F with 30-60% humidity
        if 68 <= temp <= 76 and 30 <= humidity <= 60:
            return "Optimal"
        elif 65 <= temp <= 80 and 25 <= humidity <= 70:
            return "Comfortable"
        elif temp < 65:
            return "Too Cold"
        elif temp > 80:
            return "Too Hot"
        elif humidity < 25:
            return "Too Dry"
        elif humidity > 70:
            return "Too Humid"
        else:
            return "Fair"

    def _calculate_energy_cost(self, temp: float) -> float:
        """
        Calculate estimated hourly energy cost based on temperature setting.

        Server-side calculation: lower temps in summer = higher AC cost.
        """
        # Assume 72°F is baseline ($0.50/hr)
        # Each degree colder = +$0.08/hr (AC working harder)
        # Each degree warmer = -$0.05/hr (AC working less)
        baseline = 72
        base_cost = 0.50

        if temp < baseline:
            # Cooling more = higher cost
            extra_cost = (baseline - temp) * 0.08
        else:
            # Cooling less = lower cost
            extra_cost = (baseline - temp) * 0.05

        return round(base_cost + extra_cost, 2)

    def _fahrenheit_to_celsius(self, fahrenheit: float) -> float:
        """
        Convert Fahrenheit to Celsius.

        Server-side calculation demonstrating simple but useful conversion.
        Formula: C = (F - 32) × 5/9
        """
        celsius = (fahrenheit - 32) * 5 / 9
        return round(celsius, 1)
