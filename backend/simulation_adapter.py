#imports the existing engine simulation 
import Tuning 

#allows us to store graph in memory
from io import BytesIO

#passes the engine settings from FastAPI to Tuning.py
def configure_simulation(settings):
 
    #calculates atmospheric pressure based on elevation
    Tuning.ATMOSPHERIC_PRESSURE_PSI = Tuning.calculate_atmospheric_pressure(settings.elevation)

    #sets the engine settings entered by the user
    Tuning.BASE_NATURALLY_ASPIRATED_PEAK_HP = settings.horsepower
    Tuning.USER_MAX_BOOST_PSI = settings.boostPSI
    Tuning.ACCEL_RPM_MAX = settings.redLineRPM
    Tuning.IDLE_RPM = settings.idleRPM


    #sets pressure where boost becomes active 
    Tuning.BOOST_ACTIVE_PSI = Tuning.ATMOSPHERIC_PRESSURE_PSI

    #calculates the RPM where maximum boost is reached 
    Tuning.RPM_AT_MAX_BOOST = int(Tuning.ACCEL_RPM_MAX * 0.9)

    #makes sure the boost RPM is not below acceleration minimum 
    if Tuning.RPM_AT_MAX_BOOST < Tuning.ACCEL_RPM_MIN:
        Tuning.RPM_AT_MAX_BOOST = Tuning.ACCEL_RPM_MIN + 500


    # Calculates the RPM ranges for cruising
    Tuning.CRUISE_RPM_MAX = int(Tuning.ACCEL_RPM_MIN * 0.9)
    Tuning.CRUISE_RPM_MIN = int(Tuning.IDLE_RPM * 1.5)

    # Calculates the RPM ranges for deceleration
    Tuning.DECEL_RPM_MIN = Tuning.IDLE_RPM + 100
    Tuning.DECEL_RPM_MAX = Tuning.ACCEL_RPM_MIN - 100
    
    # Sets the maximum RPM shown on the graph
    Tuning.USER_DEFINED_MAX_RPM_GAUGE = Tuning.ACCEL_RPM_MAX + 500

    # Sets the maximum MAP pressure shown on the graph
    Tuning.USER_DEFINED_MAX_MAP_GAUGE = Tuning.USER_MAX_BOOST_PSI + 7.5

    # Checks if the engine settings were passed to Tuning.py
    print(f"Configured Idle RPM: {Tuning.IDLE_RPM}")
    print(f"Configured Redline RPM: {Tuning.ACCEL_RPM_MAX}")

#runs existing simulation
def run_simulation():
    Tuning.stop_event.clear()
    Tuning.simulation_running = True

    #clears the previous simulation results
    Tuning.max_hp_achieved = 0.0
    Tuning.max_rpm_achieved = 0
    Tuning.boost_at_max_hp = 0.0
    Tuning.map_history.clear()
    Tuning.map_history.extend([0.0] * 450)

    #runs the existing simulation for 45 seconds
    Tuning.simulate_engine_data(duration_seconds=45)

    #update MAP graph with simulation reults 
    Tuning.update_graphs_for_save()

    #store graph in memory instead of saving it to a file
    image_buffer = BytesIO()
    Tuning.fig.savefig(image_buffer, format="png", dpi=300, bbox_inches="tight")

    #moves back to beginning of image data
    image_buffer.seek(0)

    #returns graph data
    return image_buffer.getvalue()
                       

