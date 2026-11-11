#imports the existing engine simulation 
from Tuning import EngineConfig, EngineSimulator, plot_result

#allows us to store graph in memory
from io import BytesIO

#passes the user's engine settings to Tuning.py
def configure_simulation(settings):

    #stores the user's inputs in the new EngineConfig class
    config = EngineConfig(
        elevation_m=settings.elevation,
        na_peak_hp=settings.horsepower,
        max_boost_psi=settings.boostPSI,
        redline_rpm=settings.redLineRPM,
        idle_rpm=settings.idleRPM
    )

    #returns the configuration for the simulation
    return config


#runs existing simulation
def run_simulation(config):
    #creates the simulator using the user's engine settings
    simulator = EngineSimulator(config)

    #runs 45 seconds of simulated engine data
    result = simulator.run(duration_s=45)

    #stores the graph in memory
    image_buffer = BytesIO()

    #creates the MAP graph using the simulation results
    plot_result(result, config, image_buffer)

    #moves back to the beginning of the image data
    image_buffer.seek(0)

    #returns the graph to FastAPI
    return image_buffer.getvalue()

