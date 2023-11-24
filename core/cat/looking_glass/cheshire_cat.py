import time

from langchain.llms import Cohere, OpenAI, AzureOpenAI
from langchain.chat_models import ChatOpenAI, AzureChatOpenAI
from langchain.base_language import BaseLanguageModel

from cat.utils import singleton
from cat.log import log
from cat.db import crud
from cat.rabbit_hole import RabbitHole
from cat.mad_hatter.mad_hatter import MadHatter
from cat.memory.long_term_memory import LongTermMemory
from cat.looking_glass.agent_manager import AgentManager
import cat.factory.llm as llms
import cat.factory.embedder as embedders
from cat.factory.custom_llm import CustomOpenAI


# main class
@singleton
class CheshireCat():
    """The Cheshire Cat.

    This is the main class that manages everything.

    Attributes
    ----------
    todo : list
        TODO TODO TODO.

    """

    def __init__(self):
        """Cat initialization.

        At init time the Cat executes the bootstrap.
        """


        # bootstrap the cat!
        # instantiate MadHatter (loads all plugins' hooks and tools)
        self.mad_hatter = MadHatter()

        # allows plugins to do something before cat components are loaded
        self.mad_hatter.execute_hook("before_cat_bootstrap", cat=self)

        # load LLM and embedder
        self.load_natural_language()

        # Load memories (vector collections and working_memory)
        self.load_memory()

        # After memory is loaded, we can get/create tools embeddings
        # every time the mad_hatter finishes syncing hooks and tools, it will notify the Cat (so it can embed tools in vector memory)
        self.mad_hatter.on_finish_plugins_sync_callback = self.embed_tools
        self.embed_tools()

        # Agent manager instance (for reasoning)
        self.agent_manager = AgentManager()

        # Rabbit Hole Instance
        self.rabbit_hole = RabbitHole(self)

        # allows plugins to do something after the cat bootstrap is complete
        self.mad_hatter.execute_hook("after_cat_bootstrap", cat=self)


    def load_natural_language(self):
        """Load Natural Language related objects.

        The method exposes in the Cat all the NLP related stuff. Specifically, it sets the language models
        (LLM and Embedder).

        Warnings
        --------
        When using small Language Models it is suggested to turn off the memories and make the main prompt smaller
        to prevent them to fail.

        See Also
        --------
        agent_prompt_prefix
        """
        # LLM and embedder
        self._llm = self.get_language_model()
        self.embedder = self.get_language_embedder()


    def get_language_model(self) -> BaseLanguageModel:
        """Large Language Model (LLM) selection at bootstrap time.

        Returns
        -------
        llm : BaseLanguageModel
            Langchain `BaseLanguageModel` instance of the selected model.

        Notes
        -----
        Bootstrapping is the process of loading the plugins, the natural language objects (e.g. the LLM), the memories,
        the *Agent Manager* and the *Rabbit Hole*.

        """
        selected_llm = crud.get_setting_by_name(name="llm_selected")

        if selected_llm is None:
            # return default LLM
            llm = llms.LLMDefaultConfig.get_llm_from_config({})

        else:
            # get LLM factory class
            selected_llm_class = selected_llm["value"]["name"]
            FactoryClass = getattr(llms, selected_llm_class)

            # obtain configuration and instantiate LLM
            selected_llm_config = crud.get_setting_by_name(name=selected_llm_class)
            try:
                llm = FactoryClass.get_llm_from_config(selected_llm_config["value"])
            except Exception as e:
                import traceback
                traceback.print_exc()
                llm = llms.LLMDefaultConfig.get_llm_from_config({})

        return llm


    def get_language_embedder(self) -> embedders.EmbedderSettings:
        """Hook into the  embedder selection.

        Allows to modify how the Cat selects the embedder at bootstrap time.

        Bootstrapping is the process of loading the plugins, the natural language objects (e.g. the LLM),
        the memories, the *Agent Manager* and the *Rabbit Hole*.

        Parameters
        ----------
        cat: CheshireCat
            Cheshire Cat instance.

        Returns
        -------
        embedder : Embeddings
            Selected embedder model.
        """
        # Embedding LLM

        selected_embedder = crud.get_setting_by_name(name="embedder_selected")

        if selected_embedder is not None:
            # get Embedder factory class
            selected_embedder_class = selected_embedder["value"]["name"]
            FactoryClass = getattr(embedders, selected_embedder_class)

            # obtain configuration and instantiate Embedder
            selected_embedder_config = crud.get_setting_by_name(name=selected_embedder_class)
            embedder = FactoryClass.get_embedder_from_config(selected_embedder_config["value"])

            return embedder

        # OpenAI embedder
        if type(self._llm) in [OpenAI, ChatOpenAI]:
            embedder = embedders.EmbedderOpenAIConfig.get_embedder_from_config(
                {
                    "openai_api_key": self._llm.openai_api_key,
                }
            )

        # Azure
        elif type(self._llm) in [AzureOpenAI, AzureChatOpenAI]:
            embedder = embedders.EmbedderAzureOpenAIConfig.get_embedder_from_config(
                {
                    "openai_api_key": self._llm.openai_api_key,
                    "openai_api_type": "azure",
                    "model": "text-embedding-ada-002",
                    # Now the only model for embeddings is text-embedding-ada-002
                    # It is also possible to use the Azure "deployment" name that is user defined
                    # when the model is deployed to Azure.
                    # "deployment": "my-text-embedding-ada-002",
                    "openai_api_base": self._llm.openai_api_base,
                    # https://learn.microsoft.com/en-us/azure/cognitive-services/openai/reference#embeddings
                    # current supported versions 2022-12-01,2023-03-15-preview, 2023-05-15
                    # Don't mix api versions https://github.com/hwchase17/langchain/issues/4775
                    "openai_api_version": "2023-05-15",
                }
            )

        # Cohere
        elif type(self._llm) in [Cohere]:
            embedder = embedders.EmbedderCohereConfig.get_embedder_from_config(
                {
                    "cohere_api_key": self._llm.cohere_api_key,
                    "model": "embed-multilingual-v2.0",
                    # Now the best model for embeddings is embed-multilingual-v2.0
                }
            )

        # Llama-cpp-python
        elif type(self._llm) in [CustomOpenAI]:
            embedder = embedders.EmbedderLlamaCppConfig.get_embedder_from_config(
                {
                    "url": self._llm.url
                }
            )

        else:
            # If no embedder matches vendor, and no external embedder is configured, we use the DumbEmbedder.
            #   `This embedder is not a model properly trained
            #    and this makes it not suitable to effectively embed text,
            #    "but it does not know this and embeds anyway".` - cit. Nicola Corbellini
            embedder = embedders.EmbedderDumbConfig.get_embedder_from_config({})

        return embedder


    def load_memory(self):
        """Load LongTerMemory and WorkingMemory."""
        # Memory
        vector_memory_config = {"cat": self, "verbose": True}
        self.memory = LongTermMemory(vector_memory_config=vector_memory_config)
        
    # loops over tools and assigns an embedding each. If an embedding is not present in vectorDB, it is created and saved
    def embed_tools(self):
        
        # retrieve from vectorDB all tool embeddings
        embedded_tools = self.memory.vectors.procedural.get_all_points()

        # easy acces to (point_id, tool_description)
        embedded_tools_ids = [t.id for t in embedded_tools]
        embedded_tools_descriptions = [t.payload["page_content"] for t in embedded_tools]

        # loop over mad_hatter tools
        for tool in self.mad_hatter.tools:
            # if the tool is not embedded 
            if tool.description not in embedded_tools_descriptions:
                # embed the tool and save it to DB
                self.memory.vectors.procedural.add_texts(
                    [tool.description],
                    [{
                        "source": "tool",
                        "when": time.time(),
                        "name": tool.name,
                        "docstring": tool.docstring
                    }],
                )

                log.warning(f"Newly embedded tool: {tool.description}")
        
        # easy access to mad hatter tools (found in plugins)
        mad_hatter_tools_descriptions = [t.description for t in self.mad_hatter.tools]

        # loop over embedded tools and delete the ones not present in active plugins
        points_to_be_deleted = []
        for id, descr in zip(embedded_tools_ids, embedded_tools_descriptions):
            # if the tool is not active, it inserts it in the list of points to be deleted
            if descr not in mad_hatter_tools_descriptions:
                log.warning(f"Deleting embedded tool: {descr}")
                points_to_be_deleted.append(id)

        # delete not active tools
        if len(points_to_be_deleted) > 0:
            self.memory.vectors.vector_db.delete(
                collection_name="procedural",
                points_selector=points_to_be_deleted
            )

    def send_ws_message(self, content: str, msg_type = "notification"):
        log.error("No websocket connection open")