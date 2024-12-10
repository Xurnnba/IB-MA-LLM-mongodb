import certifi
from pymongo import MongoClient, ASCENDING
import json
import logging
import sys
from autogen_judging import MultiAgentJudgingSystem
import datetime
import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# Configure logging with both file and console handlers
def setup_logging(log_file: str = 'app.log'):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

class ProgressTracker:
    def __init__(self, checkpoint_file: str = 'progress_checkpoint.json'):
        self.checkpoint_file = checkpoint_file
        self.current_progress = self.load_checkpoint()

    def load_checkpoint(self) -> Dict[str, Any]:
        try:
            with open(self.checkpoint_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                'current_group': 0,
                'current_scenario': 0,
                'current_response': 0,
                'completed': False
            }

    def save_checkpoint(self, group: int, scenario: int, response: int):
        self.current_progress.update({
            'current_group': group,
            'current_scenario': scenario,
            'current_response': response,
            'timestamp': datetime.datetime.now().isoformat()
        })
        with open(self.checkpoint_file, 'w') as f:
            json.dump(self.current_progress, f, indent=2)

    def is_completed(self, group: int, scenario: int, response: int) -> bool:
        return (group < self.current_progress['current_group'] or
                (group == self.current_progress['current_group'] and
                 scenario < self.current_progress['current_scenario']) or
                (group == self.current_progress['current_group'] and
                 scenario == self.current_progress['current_scenario'] and
                 response <= self.current_progress['current_response']))

class RobustJudgingSystem:
    def __init__(self, api_key: str, mongodb_uri: str, database_name: str, llm_model: str, api_type: str):
        """Initialize the robust judging system.

        Args:
            api_key (str): API key for the LLM service
            mongodb_uri (str): MongoDB connection URI
            database_name (str): Name of the MongoDB database
            llm_model (str): Name of the LLM model used for judging system
            api_type (str): Type of the API used for judging system
        """
        self.api_key = api_key
        self.mongodb_uri = mongodb_uri
        self.database_name = database_name
        self.llm_model = llm_model
        self.api_type = api_type
        self.progress_tracker = ProgressTracker()
        setup_logging()
        
    def connect_to_mongodb(self):
        try:
            client = MongoClient(self.mongodb_uri, tlsCAFile=certifi.where())
            db = client[self.database_name]
            logging.info(f"Connected to MongoDB database: {self.database_name}")
            return db
        except Exception as e:
            logging.error(f"Error connecting to MongoDB: {e}")
            return None

    def save_evaluation(self, evaluation: Dict[str, Any], results_file: str = 'results.json'):
        try:
            # Load existing results
            try:
                with open(results_file, 'r') as f:
                    results = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                results = []
            
            results.append(evaluation)
            
            # Write back to file
            with open(results_file, 'w') as f:
                json.dump(results, f, indent=2)
                
        except Exception as e:
            logging.error(f"Error saving evaluation: {e}")
            # Create backup of the evaluation
            with open(f'backup_evaluation_{datetime.datetime.now().isoformat()}.json', 'w') as f:
                json.dump(evaluation, f, indent=2)

    def process_scenario(self, judging_system, scenario, ma_setting, group_num: int, scenario_num: int):
        scenario_responses = scenario['responses']
        for resp_idx, response in enumerate(scenario_responses):
            # Check if this combination has already been processed
            if self.progress_tracker.is_completed(group_num, scenario_num, resp_idx):
                logging.info(f"Skipping already processed response {resp_idx} in scenario {scenario_num}, group {group_num}")
                continue

            try:
                responses = {
                    'male_response': response['response_1'],
                    'female_response': response['response_2']
                }
                evaluations = {
                    'response': judging_system.evaluate_responses(responses, ma_setting, self.llm_model, self.api_type),
                    'response_model': response['model'],
                    'scenario_description': scenario['description'],
                    'timestamp': datetime.datetime.now().isoformat(),
                    'group': group_num,
                    'scenario_index': scenario_num,
                    'response_index': resp_idx
                }
                
                self.save_evaluation(evaluations)
                self.progress_tracker.save_checkpoint(group_num, scenario_num, resp_idx)
                logging.info(f"Successfully processed group {group_num}, scenario {scenario_num}, response {resp_idx}")
                
            except Exception as e:
                logging.error(f"Error processing response {resp_idx} in scenario {scenario_num}, group {group_num}: {e}")
                # Save current state and raise exception to trigger retry
                self.progress_tracker.save_checkpoint(group_num, scenario_num, resp_idx)
                raise

    def run(self):
        db = self.connect_to_mongodb()
        if db is None:
            logging.error("Failed to connect to MongoDB. Exiting.")
            return

        try:
            scenarios_collection = db['scenarios']
            masettings_collection = db['masettings']
            ma_setting = masettings_collection.find_one()
            judging_system = MultiAgentJudgingSystem(self.api_key, self.llm_model, self.api_type)
            
            groups = scenarios_collection.find({}).sort("group", ASCENDING)
            
            for group_idx, group in enumerate(groups):
                scenarios = group['scenarios']
                logging.info(f"Processing group: {group['group']}")
                
                for scenario_idx, scenario in enumerate(scenarios):
                    try:
                        self.process_scenario(judging_system, scenario, ma_setting, 
                                           group['group'], scenario_idx)
                    except Exception as e:
                        logging.error(f"Failed to process scenario {scenario_idx} in group {group['group']}: {e}")
                        # Continue with next scenario
                        continue
                        
                logging.info(f"Completed processing group {group['group']}")
                
        except Exception as e:
            logging.error(f"Unexpected error in main processing loop: {e}")
            raise

def main():
    # Configuration
    MONGODB_URI = 'mongodb+srv://epicawesome450:3Bjat9tTYeMwkOuq@icirbias.5wpqw.mongodb.net/'
    DATABASE_NAME = 'ICIRBias'
    # hard coded because of the limitations of this script
    
    API_KEY = os.getenv('API_KEY')
    LLM_MODEL = os.getenv('LLM_MODEL')
    API_TYPE = os.getenv('API_TYPE')
    # Initialize and run the robust judging system
    judging_system = RobustJudgingSystem(API_KEY, MONGODB_URI, DATABASE_NAME, LLM_MODEL, API_TYPE)
    judging_system.run()

if __name__ == "__main__":
    main()